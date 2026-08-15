# ============================================================
# NTUST SiPh Lab - Research Output Service
#
# 上下游：
#   Admin Route -> ResearchForm -> ResearchService -> ResearchOutput -> DB
#                                              \-> ResearchOutputPerson（關聯）
#                                              \-> Redirect（slug 變更 AC-10）
#                                              \-> AuditLog
#   ResearchService -> PublishValidator（SAI §15.2 發布門檻）
#   ResearchService -> MediaService（主圖）
#
# 檔案路徑：
#   app/services/research_service.py
#
# 建立日期：2026-08-14
# 最後重大修改：2026-08-14
# 版本：v1.0
#
# 模組定位與責任邊界：
#   研究成果 CRUD、人物關聯維護與發布流程的交易邊界。
#   對應 SAI §9.3 的管理更新 request flow：
#     form validation -> service.update -> sanitize/normalize
#     -> DB transaction -> slug 變更建 Redirect -> AuditLog -> PRG
#
#   責任邊界（不得做的事）：
#     - 不得 render HTML / flash（那是 route）。
#     - 不得直接讀 request。
#     - 不得繞過 PublishValidator 設定 published。
#
# 輸入 -> 處理 -> 輸出 Pipeline：
#   create/update:
#     欄位 dict -> 正規化（含 DOI normalize、keywords 去重）
#       -> slug 唯一化 -> [曾公開則建 Redirect]
#       -> 同步人物關聯 -> AuditLog -> commit
#
# 主要 Class / Function：
#   ResearchServiceError
#   ResearchService.create / update / publish / unpublish / archive
#   ResearchService.set_featured
#   ResearchService.sync_people(output, entries)
#   ResearchService.attach_hero_image / remove_hero_image
#
# 依賴套件：
#   sqlalchemy, app.models, app.utils.*, app.services.*
#
# 環境變數：無。
#
# 資料庫使用方式：
#   research_outputs、research_output_people、redirects、audit_logs。
#   單一 transaction，由本模組 commit。
#
# Error Handling / Fallback：
#   - 業務規則違反拋 ResearchServiceError（訊息可直接顯示）。
#   - IntegrityError 轉為友善訊息（多半是 slug 或重複關聯衝突）。
#   - 任何例外保證 rollback。
#
# 特殊機制（人物關聯同步）：
#   sync_people 採「全量替換」而非「逐筆 diff」。
#   為什麼：作者順序是有意義的資料，逐筆 diff 需要處理
#   「順序變更但成員不變」的情況，邏輯複雜且容易出錯。
#   關聯數量是個位數，全量替換的成本可忽略，且語意清晰：
#   表單送什麼，DB 就是什麼。
#
#   注意：全量替換會刪除再新增 ResearchOutputPerson row，
#   其 id 會改變。這是可接受的，因為該表沒有對外暴露的識別。
#
# 已知限制與禁止事項：
#   1. 禁止虛構 DOI / venue / 出版狀態（SAI §14.3）。
#      本服務只做格式正規化，不會補值。
#   2. 禁止在未發布狀態設 is_featured（SAI §15.2，
#      由 PublishValidator 與 set_featured 雙重把關）。
#   3. 外部共同作者不建立 Person；請使用 authors_display_text。
#
# 維護契約：
#   1. 任何會改變 slug 的路徑都必須經過 _apply_slug()。
#   2. 新增欄位時同步更新 _assign_fields 與 admin form，
#      否則會出現「欄位存在但無法維護」。
#
# 驗證方式：
#   pytest tests/test_research.py
# ============================================================

from __future__ import annotations

import logging
from datetime import date

from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.audit_log import AuditLog
from app.models.mixins import (
    AuditAction,
    ContributorRole,
    OutputType,
    PublishStatus,
)
from app.models.person import Person
from app.models.redirect import Redirect
from app.models.research_output import ResearchOutput, ResearchOutputPerson
from app.services.media_service import MediaService
from app.services.publish_validator import PublishValidator
from app.utils.slugs import ensure_unique_slug, slugify
from app.utils.validators import (
    normalize_multiline,
    normalize_text,
    normalize_url,
    parse_tag_input,
    validate_year,
)

logger = logging.getLogger(__name__)

#: research_outputs.slug 欄位長度 VARCHAR(160)，保留後綴空間。
_SLUG_MAX = 150


class ResearchServiceError(RuntimeError):
    """業務規則違反；訊息可直接顯示給管理員。"""


class ResearchService:
    """研究成果 CRUD、關聯與發布（SAI §9.3、§15.2）。"""

    # ------------------------------------------------------------------
    # 內部工具
    # ------------------------------------------------------------------
    @staticmethod
    def _public_path(slug: str) -> str:
        return f"/research/{slug}"

    @staticmethod
    def _apply_slug(output: ResearchOutput, desired: str | None, was_published: bool) -> None:
        """設定 slug；曾公開且變更時建立 301（AC-10）。

        was_published 的判斷理由與 person_service 相同：
        必須用「修改前」的狀態決定是否需要 redirect。
        """
        old_slug = output.slug

        base = desired.strip() if desired and desired.strip() else None

        if not base:
            if old_slug:
                # 已有 slug 且未指定新值 -> 保持不變。
                # 理由與 person_service._apply_slug 相同：
                # SAI §4.2「slug 一旦公開即視為永久識別」，
                # 修改標題不應該連帶改變已公開的網址。
                return
            # 首次產生：優先英文標題（slug 為英文小寫，
            # 英文標題轉出來的可讀性優於中文拼音）。
            source = output.title_en or output.title_zh or "research-output"
            base = slugify(source, max_length=_SLUG_MAX, fallback_prefix="research")
        else:
            base = slugify(base, max_length=_SLUG_MAX, fallback_prefix="research")

        new_slug = ensure_unique_slug(
            ResearchOutput, base, exclude_id=output.id, max_length=_SLUG_MAX
        )

        if old_slug and old_slug != new_slug and was_published:
            Redirect.record(
                old_path=ResearchService._public_path(old_slug),
                new_path=ResearchService._public_path(new_slug),
                reason="slug_changed",
            )
            logger.info("成果 slug 變更並建立 301：%s -> %s", old_slug, new_slug)

        output.slug = new_slug

    @staticmethod
    def _parse_publication_date(value) -> date | None:
        """把表單日期轉為 date；不合法回 None。

        接受 date 物件或 ISO 字串。不合法時回 None 而非拋錯：
        publication_date 是選填欄位（SAI §8.4），
        格式錯誤不應阻擋整筆儲存 —— 年份欄位已提供時序資訊。
        """
        if not value:
            return None
        if isinstance(value, date):
            return value
        try:
            return date.fromisoformat(str(value).strip())
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _assign_fields(output: ResearchOutput, data: dict) -> None:
        """把已驗證的表單資料寫入成果欄位。"""
        output_type = data.get("output_type")
        if output_type in OutputType.ALL:
            output.output_type = output_type

        year = validate_year(data.get("year"))
        if year:
            output.year = year

        output.publication_date = ResearchService._parse_publication_date(
            data.get("publication_date")
        )

        output.title_zh = normalize_multiline(data.get("title_zh"))
        output.title_en = normalize_multiline(data.get("title_en"))

        output.summary_zh = normalize_multiline(data.get("summary_zh"))
        output.summary_en = normalize_multiline(data.get("summary_en"))
        output.problem_zh = normalize_multiline(data.get("problem_zh"))
        output.problem_en = normalize_multiline(data.get("problem_en"))
        output.method_zh = normalize_multiline(data.get("method_zh"))
        output.method_en = normalize_multiline(data.get("method_en"))
        output.results_zh = normalize_multiline(data.get("results_zh"))
        output.results_en = normalize_multiline(data.get("results_en"))
        output.significance_zh = normalize_multiline(data.get("significance_zh"))
        output.significance_en = normalize_multiline(data.get("significance_en"))

        output.venue = normalize_text(data.get("venue"))

        # DOI：只做正規化，不補值（SAI §14.3 禁止虛構）。
        raw_doi = data.get("doi")
        if raw_doi is not None:
            normalized = ResearchOutput.normalize_doi(raw_doi)
            if raw_doi and normalize_text(raw_doi) and normalized is None:
                # 有輸入但無法解析 -> 是使用者輸入錯誤，必須明確告知，
                # 而不是靜默丟棄（那會讓管理者以為已存檔）。
                raise ResearchServiceError(
                    "DOI 格式不正確。請輸入形如 10.1109/JLT.2026.1234567 的值，"
                    "或完整的 https://doi.org/... 網址。"
                )
            output.doi = normalized

        output.external_url = normalize_url(data.get("external_url"))
        output.github_url = normalize_url(data.get("github_url"))
        output.dataset_url = normalize_url(data.get("dataset_url"))
        output.authors_display_text = normalize_multiline(data.get("authors_display_text"))

        raw_keywords = data.get("keywords")
        if isinstance(raw_keywords, str):
            output.keywords = parse_tag_input(raw_keywords)
        elif raw_keywords is not None:
            output.keywords = list(raw_keywords)

        output.hero_image_alt_zh = normalize_text(data.get("hero_image_alt_zh"))
        output.hero_image_alt_en = normalize_text(data.get("hero_image_alt_en"))

        if data.get("sort_order") is not None:
            try:
                output.sort_order = int(data["sort_order"])
            except (TypeError, ValueError):
                pass

        output.seo_title_zh = normalize_text(data.get("seo_title_zh"))
        output.seo_description_zh = normalize_text(data.get("seo_description_zh"))

    @staticmethod
    def _commit(action_summary: str) -> None:
        """提交交易；失敗時 rollback 並轉為業務例外。"""
        try:
            db.session.commit()
        except IntegrityError as exc:
            db.session.rollback()
            logger.warning("成果寫入違反約束（%s）：%s", action_summary, exc)
            raise ResearchServiceError(
                "儲存失敗：slug 可能已被使用，或同一位成員被重複關聯。"
            ) from exc
        except Exception as exc:  # noqa: BLE001
            db.session.rollback()
            logger.exception("成果寫入失敗（%s）", action_summary)
            raise ResearchServiceError(f"儲存失敗：{exc}") from exc

    # ------------------------------------------------------------------
    # 人物關聯
    # ------------------------------------------------------------------
    @staticmethod
    def sync_people(output: ResearchOutput, entries) -> None:
        """全量同步成果的 Lab 人物關聯（見檔頭「特殊機制」）。

        Args:
            entries: 可為
                     - [person_id, ...]（順序即作者順序）
                     - [{"person_id": int, "role": str}, ...]

        Raises:
            ResearchServiceError: 指定的 person_id 不存在
                                  （SAI §15.2「關聯 person 必須存在」）。
        """
        normalized: list[tuple[int, str]] = []
        seen: set[int] = set()

        for item in entries or []:
            if isinstance(item, dict):
                person_id = item.get("person_id")
                role = item.get("role") or ContributorRole.AUTHOR
            else:
                person_id = item
                role = ContributorRole.AUTHOR

            try:
                person_id = int(person_id)
            except (TypeError, ValueError):
                continue

            # 表單可能送出重複值；去重而非拋錯（UNIQUE 約束會擋，
            # 但在這裡先處理可以給出更好的使用者體驗）。
            if person_id in seen:
                continue

            if role not in ContributorRole.ALL:
                role = ContributorRole.AUTHOR

            seen.add(person_id)
            normalized.append((person_id, role))

        # 驗證所有 person 都存在。
        if normalized:
            existing_ids = set(
                db.session.scalars(
                    db.select(Person.id).where(Person.id.in_([pid for pid, _ in normalized]))
                ).all()
            )
            missing = [pid for pid, _ in normalized if pid not in existing_ids]
            if missing:
                raise ResearchServiceError(
                    f"關聯的成員不存在（id: {', '.join(map(str, missing))}）。"
                )

        # 全量替換。
        output.person_links.clear()
        # flush 讓 delete-orphan 先執行，避免與新增的 row 在
        # UNIQUE(research_output_id, person_id) 上衝突。
        db.session.flush()

        for index, (person_id, role) in enumerate(normalized):
            output.person_links.append(
                ResearchOutputPerson(
                    person_id=person_id,
                    contributor_role=role,
                    sort_order=index,
                )
            )

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    @staticmethod
    def create(
        data: dict, admin_user_id: int | None = None, ip_address: str | None = None
    ) -> ResearchOutput:
        """建立研究成果（預設 draft）。"""
        year = validate_year(data.get("year"))
        if not year:
            raise ResearchServiceError("年份為必填，且需為 1900-2200 之間的西元年。")

        if not (normalize_multiline(data.get("title_zh")) or normalize_multiline(data.get("title_en"))):
            raise ResearchServiceError("標題至少需填寫一個語言版本。")

        output = ResearchOutput(
            slug="",
            year=year,
            output_type=data.get("output_type") if data.get("output_type") in OutputType.ALL else OutputType.OTHER,
            publish_status=PublishStatus.DRAFT,
        )

        ResearchService._assign_fields(output, data)

        db.session.add(output)
        db.session.flush()

        ResearchService._apply_slug(output, data.get("slug"), was_published=False)
        ResearchService.sync_people(output, data.get("people"))

        AuditLog.write(
            action=AuditAction.CREATE,
            entity_type="research_output",
            entity_id=output.id,
            summary=f"建立研究成果 {output.display_title}（{output.slug}）",
            admin_user_id=admin_user_id,
            ip_address=ip_address,
        )

        ResearchService._commit(f"create research {output.slug}")
        return output

    @staticmethod
    def update(
        output: ResearchOutput,
        data: dict,
        admin_user_id: int | None = None,
        ip_address: str | None = None,
    ) -> ResearchOutput:
        """更新研究成果；slug 變更且曾公開時建立 301（AC-10）。"""
        was_published = output.publish_status == PublishStatus.PUBLISHED
        old_slug = output.slug

        ResearchService._assign_fields(output, data)
        ResearchService._apply_slug(output, data.get("slug"), was_published=was_published)

        if "people" in data:
            ResearchService.sync_people(output, data.get("people"))

        summary = f"更新研究成果 {output.display_title}（{output.slug}）"
        if old_slug != output.slug:
            summary += f"；slug {old_slug} -> {output.slug}"

        AuditLog.write(
            action=AuditAction.UPDATE,
            entity_type="research_output",
            entity_id=output.id,
            summary=summary,
            admin_user_id=admin_user_id,
            ip_address=ip_address,
        )

        ResearchService._commit(f"update research {output.slug}")
        return output

    # ------------------------------------------------------------------
    # 狀態變更
    # ------------------------------------------------------------------
    @staticmethod
    def publish(
        output: ResearchOutput, admin_user_id: int | None = None, ip_address: str | None = None
    ) -> ResearchOutput:
        """發布成果（AC-09）。未通過門檻時拒絕。"""
        result = PublishValidator.validate_research(output)
        if not result.is_valid:
            raise ResearchServiceError(
                "無法發布，請先修正以下問題：\n- " + "\n- ".join(result.error_messages())
            )

        output.publish_status = PublishStatus.PUBLISHED

        AuditLog.write(
            action=AuditAction.PUBLISH,
            entity_type="research_output",
            entity_id=output.id,
            summary=f"發布研究成果 {output.display_title}（{output.slug}）",
            admin_user_id=admin_user_id,
            ip_address=ip_address,
        )

        ResearchService._commit(f"publish research {output.slug}")
        return output

    @staticmethod
    def unpublish(
        output: ResearchOutput, admin_user_id: int | None = None, ip_address: str | None = None
    ) -> ResearchOutput:
        """退回草稿。同時取消 featured —— 未發布內容不得為精選。"""
        output.publish_status = PublishStatus.DRAFT
        output.is_featured = False

        AuditLog.write(
            action=AuditAction.UPDATE,
            entity_type="research_output",
            entity_id=output.id,
            summary=f"取消發布研究成果 {output.display_title}（{output.slug}）",
            admin_user_id=admin_user_id,
            ip_address=ip_address,
        )
        ResearchService._commit(f"unpublish research {output.slug}")
        return output

    @staticmethod
    def archive(
        output: ResearchOutput, admin_user_id: int | None = None, ip_address: str | None = None
    ) -> ResearchOutput:
        """封存成果（SAI §7.4）。同時取消 featured。"""
        output.publish_status = PublishStatus.ARCHIVED
        output.is_featured = False

        AuditLog.write(
            action=AuditAction.ARCHIVE,
            entity_type="research_output",
            entity_id=output.id,
            summary=f"封存研究成果 {output.display_title}（{output.slug}）",
            admin_user_id=admin_user_id,
            ip_address=ip_address,
        )
        ResearchService._commit(f"archive research {output.slug}")
        return output

    @staticmethod
    def set_featured(
        output: ResearchOutput,
        featured: bool,
        admin_user_id: int | None = None,
        ip_address: str | None = None,
    ) -> ResearchOutput:
        """設定/取消精選（SAI §15.2：published 才能 featured）。

        Raises:
            ResearchServiceError: 嘗試把未發布成果設為精選。
        """
        if featured and output.publish_status != PublishStatus.PUBLISHED:
            raise ResearchServiceError("只有已發布的成果才能設為精選（SAI §15.2）。")

        output.is_featured = bool(featured)

        AuditLog.write(
            action=AuditAction.UPDATE,
            entity_type="research_output",
            entity_id=output.id,
            summary=(
                f"{'設為精選' if featured else '取消精選'} "
                f"{output.display_title}（{output.slug}）"
            ),
            admin_user_id=admin_user_id,
            ip_address=ip_address,
        )
        ResearchService._commit(f"feature research {output.slug}")
        return output

    # ------------------------------------------------------------------
    # 主圖
    # ------------------------------------------------------------------
    @staticmethod
    def attach_hero_image(
        output: ResearchOutput,
        file_storage,
        alt_zh: str | None = None,
        admin_user_id: int | None = None,
        ip_address: str | None = None,
    ) -> ResearchOutput:
        """上傳並綁定主圖。先傳新的、成功後才刪舊的（理由同人物照片）。"""
        old_key = output.hero_image_path
        saved = MediaService.save_image(file_storage, purpose="research")

        output.hero_image_path = saved.key
        if alt_zh:
            output.hero_image_alt_zh = normalize_text(alt_zh)

        AuditLog.write(
            action=AuditAction.UPDATE,
            entity_type="research_output",
            entity_id=output.id,
            summary=f"更新成果主圖 {output.display_title}（{saved.key}）",
            admin_user_id=admin_user_id,
            ip_address=ip_address,
        )
        ResearchService._commit(f"attach hero {output.slug}")

        if old_key and old_key != saved.key:
            try:
                MediaService.delete_image(old_key)
            except Exception:  # noqa: BLE001
                logger.warning("刪除舊主圖失敗（已成為孤兒檔案）：%s", old_key)

        return output

    @staticmethod
    def remove_hero_image(
        output: ResearchOutput, admin_user_id: int | None = None, ip_address: str | None = None
    ) -> ResearchOutput:
        """移除主圖（先解除引用再刪檔）。"""
        old_key = output.hero_image_path
        if not old_key:
            return output

        output.hero_image_path = None
        output.hero_image_alt_zh = None
        output.hero_image_alt_en = None

        AuditLog.write(
            action=AuditAction.UPDATE,
            entity_type="research_output",
            entity_id=output.id,
            summary=f"移除成果主圖 {output.display_title}",
            admin_user_id=admin_user_id,
            ip_address=ip_address,
        )
        ResearchService._commit(f"remove hero {output.slug}")

        try:
            MediaService.delete_image(old_key)
        except Exception:  # noqa: BLE001
            logger.warning("刪除主圖檔案失敗：%s", old_key)

        return output
