# ============================================================
# NTUST SiPh Lab - Person Service
#
# 上下游：
#   Admin Route -> PersonForm -> PersonService -> Person -> SQLAlchemy -> DB
#                                            \-> Redirect（slug 變更）
#                                            \-> AuditLog（稽核）
#   PersonService -> PublishValidator（發布門檻）
#   PersonService -> MediaService（照片上傳/刪除）
#   scripts/seed_from_google_sites.py -> PersonService.create()
#
# 檔案路徑：
#   app/services/person_service.py
#
# 建立日期：2026-08-14
# 最後重大修改：2026-08-14
# 版本：v1.0
#
# 模組定位與責任邊界：
#   人物 CRUD 與狀態轉換的交易邊界。SAI §9.1 明定 Route 不得把
#   複雜 DB 邏輯塞在裡面、Service 不得 render HTML。
#
#   本模組最關鍵的責任是 SAI §7.5「在學轉畢業流程」與 §9.3
#   「slug 變更 -> 建立 Redirect -> 寫 AuditLog」必須是原子操作。
#
#   責任邊界（不得做的事）：
#     - 不得 render template 或使用 flash（那是 route）。
#     - 不得直接讀 request（欄位由 route 解析後傳入）。
#     - 不得繞過 PublishValidator 直接把 publish_status 設為 published。
#
# 輸入 -> 處理 -> 輸出 Pipeline：
#   create/update:
#     欄位 dict -> 正規化（validators）-> slug 唯一化
#       -> 偵測 slug 變更 -> [若原本已公開] 建立 Redirect
#       -> 寫入欄位 -> AuditLog -> commit -> Person
#   graduate:
#     person + 畢業資訊 -> status=alumni -> 補欄位
#       -> AuditLog(graduate) -> commit
#
# 主要 Class / Function：
#   PersonServiceError            - 業務規則違反
#   PersonService.create(data, ...)
#   PersonService.update(person, data, ...)
#   PersonService.publish(person, ...)
#   PersonService.archive(person, ...)
#   PersonService.graduate(person, ...)
#   PersonService.attach_photo / remove_photo
#
# 依賴套件：
#   sqlalchemy, app.models, app.utils.slugs, app.utils.validators,
#   app.services.publish_validator, app.services.media_service
#
# 環境變數：無（間接透過 MediaService 使用 storage 設定）。
#
# 資料庫使用方式：
#   people、redirects、audit_logs。所有寫入在單一 transaction 內，
#   由本模組呼叫 db.session.commit()。
#
# Error Handling / Fallback：
#   - 業務規則違反（例如發布未通過驗證）拋 PersonServiceError，
#     訊息可直接顯示給管理員。
#   - IntegrityError（slug 競態）會 rollback 並重試一次；
#     再失敗則拋 PersonServiceError。
#   - 任何例外都保證 rollback，不留下半套資料。
#
# 特殊機制（Transaction 與 slug 保護）：
#   1. slug 變更只在「原本已發布過」時才建立 Redirect。
#      draft 階段改 slug 不需要 redirect，因為那個 URL 從未公開，
#      建立 redirect 只會累積垃圾資料（SAI §4.2 的規則是
#      「一旦公開即視為永久識別」）。
#   2. graduate() 不改變 id 與 slug（ADR-008 / AC-06），
#      因此不產生 redirect —— 這是刻意的：個人 URL 在畢業後
#      仍應可用。
#
# 已知限制與禁止事項：
#   1. 禁止在畢業流程中建立新的 Person（ADR-008）。
#   2. 禁止硬刪除有關聯成果的人物（SAI §7.6）。
#   3. 禁止直接指派 person.publish_status = "published"；
#      必須透過 publish() 以確保通過驗證與稽核。
#
# 維護契約：
#   1. 任何新增的「會改變 slug 的路徑」都必須經過 _apply_slug()。
#   2. 任何狀態變更都必須寫 AuditLog，否則違反 SAI §1.1
#      「重要管理操作可回溯」。
#
# 驗證方式：
#   pytest tests/test_people.py
# ============================================================

from __future__ import annotations

import logging

from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.audit_log import AuditLog
from app.models.mixins import AuditAction, PersonStatus, PublishStatus
from app.models.person import Person
from app.models.redirect import Redirect
from app.services.media_service import MediaService
from app.services.publish_validator import PublishValidator
from app.utils.slugs import ensure_unique_slug, slugify, suggest_person_slug
from app.utils.validators import (
    normalize_email,
    normalize_multiline,
    normalize_text,
    normalize_url,
    parse_tag_input,
    validate_year,
)

logger = logging.getLogger(__name__)

#: people.slug 欄位長度（VARCHAR(120)），保留後綴空間。
_SLUG_MAX = 110


class PersonServiceError(RuntimeError):
    """業務規則違反；訊息可直接顯示給管理員。"""


class PersonService:
    """人物 CRUD、發布與畢業轉換（SAI §7.5、§9.3）。"""

    # ------------------------------------------------------------------
    # 內部工具
    # ------------------------------------------------------------------
    @staticmethod
    def _public_path(slug: str) -> str:
        """人物公開路徑。集中於此避免各處字串拼接不一致。"""
        return f"/people/{slug}"

    @staticmethod
    def _apply_slug(person: Person, desired: str | None, was_published: bool) -> None:
        """設定 slug，必要時建立 301 Redirect。

        Args:
            desired: 管理者輸入的 slug；空值時由姓名自動產生。
            was_published: 這筆資料在本次修改前是否曾公開。

        為什麼要傳 was_published 而不是讀 person.publish_status：
          呼叫端可能在同一次操作中同時改 slug 與改狀態。
          必須以「修改前」的狀態判斷是否需要 redirect，
          否則「draft 改 slug 同時發布」會誤建 redirect，
          而「published 改 slug 同時轉 draft」會漏建。
        """
        old_slug = person.slug

        base = desired.strip() if desired and desired.strip() else None

        if not base:
            if old_slug:
                # 已經有 slug 且管理者沒有明確指定新值 -> 保持不變。
                #
                # 為什麼不重新由姓名產生：
                #   SAI §4.2 明定「slug 一旦公開即視為永久識別」。
                #   若每次更新都重算，管理者只是修正英文姓名的拼寫，
                #   公開 URL 就會跟著改變並產生一筆非預期的 301。
                #   雖然舊連結仍可到達，但 canonical 變動會影響
                #   搜尋引擎已建立的索引，屬於不必要的損失。
                #   要變更 slug 必須在表單中明確輸入新值。
                return
            base = suggest_person_slug(person.name_zh, person.name_en)
        else:
            # 管理者明確輸入的值仍必須正規化 —— 表單允許輸入
            # 任意文字，未經 slugify 會產生含空白或大寫的壞 URL。
            base = slugify(base, max_length=_SLUG_MAX, fallback_prefix="person")

        new_slug = ensure_unique_slug(
            Person, base, exclude_id=person.id, max_length=_SLUG_MAX
        )

        if old_slug and old_slug != new_slug and was_published:
            # 只有曾公開的 URL 才需要 301（見檔頭「特殊機制 1」）。
            Redirect.record(
                old_path=PersonService._public_path(old_slug),
                new_path=PersonService._public_path(new_slug),
                reason="slug_changed",
            )
            logger.info("人物 slug 變更並建立 301：%s -> %s", old_slug, new_slug)

        person.slug = new_slug

    @staticmethod
    def _assign_fields(person: Person, data: dict) -> None:
        """把已驗證的表單資料寫入 person 欄位。

        所有文字都經過 normalize_*，理由見 utils/validators.py：
        零寬字元與空白差異會破壞 legacy difference_report 的
        exact match 比對（SAI §22.5）。
        """
        person.name_zh = normalize_text(data.get("name_zh")) or person.name_zh
        person.name_en = normalize_text(data.get("name_en"))

        status = data.get("status")
        if status in PersonStatus.ALL:
            person.status = status

        person.degree = normalize_text(data.get("degree"))
        person.title_zh = normalize_text(data.get("title_zh"))
        person.title_en = normalize_text(data.get("title_en"))
        person.education_zh = normalize_multiline(data.get("education_zh"))
        person.education_en = normalize_multiline(data.get("education_en"))

        person.entry_year = validate_year(data.get("entry_year"))
        person.graduation_year = validate_year(data.get("graduation_year"))

        person.research_focus_zh = normalize_multiline(data.get("research_focus_zh"))
        person.research_focus_en = normalize_multiline(data.get("research_focus_en"))
        person.thesis_title_zh = normalize_multiline(data.get("thesis_title_zh"))
        person.thesis_title_en = normalize_multiline(data.get("thesis_title_en"))
        person.bio_zh = normalize_multiline(data.get("bio_zh"))
        person.bio_en = normalize_multiline(data.get("bio_en"))

        # skills 接受字串（tag input）或已解析的 list。
        raw_skills = data.get("skills")
        if isinstance(raw_skills, str):
            person.skills = parse_tag_input(raw_skills)
        elif raw_skills is not None:
            person.skills = list(raw_skills)

        person.current_affiliation = normalize_text(data.get("current_affiliation"))
        person.current_position = normalize_text(data.get("current_position"))
        person.destination_public = bool(data.get("destination_public"))

        person.email_public = normalize_email(data.get("email_public"))
        person.orcid_url = normalize_url(data.get("orcid_url"))
        person.scholar_url = normalize_url(data.get("scholar_url"))
        person.github_url = normalize_url(data.get("github_url"))
        person.linkedin_url = normalize_url(data.get("linkedin_url"))
        person.external_url = normalize_url(data.get("external_url"))
        person.external_url_label = normalize_text(data.get("external_url_label"))

        person.photo_alt_zh = normalize_text(data.get("photo_alt_zh"))
        # photo_alt_en 已不由後台表單維護（公開模板一律讀 photo_alt_zh）。
        # 只在呼叫端明確傳入時才寫入 —— 否則表單每次送出都會
        # 因為 data 沒有這個 key 而把既有資料清成 None。
        if "photo_alt_en" in data:
            person.photo_alt_en = normalize_text(data.get("photo_alt_en"))

        if data.get("sort_order") is not None:
            try:
                person.sort_order = int(data["sort_order"])
            except (TypeError, ValueError):
                # 保留原值而非拋錯：排序是次要欄位，
                # 不應該讓整筆儲存失敗。
                pass

        if "is_featured" in data:
            person.is_featured = bool(data.get("is_featured"))

        person.seo_title_zh = normalize_text(data.get("seo_title_zh"))
        person.seo_description_zh = normalize_text(data.get("seo_description_zh"))

        # legacy 旗標：一旦補齊研究焦點就自動解除提醒。
        # 為什麼自動解除：這個旗標的唯一用途是「提醒補資料」，
        # 資料補齊後仍保留旗標只會讓 Dashboard 出現永久噪音。
        if person.legacy_pending_detail and (
            person.research_focus_zh or person.research_focus_en
        ):
            person.legacy_pending_detail = False

    @staticmethod
    def _commit(action_summary: str) -> None:
        """提交交易；失敗時 rollback 並轉為業務例外。"""
        try:
            db.session.commit()
        except IntegrityError as exc:
            db.session.rollback()
            logger.warning("人物寫入違反約束（%s）：%s", action_summary, exc)
            raise PersonServiceError(
                "儲存失敗：slug 可能已被使用，請更換後再試。"
            ) from exc
        except Exception as exc:  # noqa: BLE001
            db.session.rollback()
            logger.exception("人物寫入失敗（%s）", action_summary)
            raise PersonServiceError(f"儲存失敗：{exc}") from exc

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    @staticmethod
    def create(data: dict, admin_user_id: int | None = None, ip_address: str | None = None) -> Person:
        """建立人物（預設為 draft）。

        刻意「不」在建立時允許直接發布：
          SAI §15.1 的發布門檻需要完整欄位，而新增表單常常是
          分次填寫。強制先存草稿再發布，可確保每次發布都經過
          publish() 的驗證與稽核。
        """
        name_zh = normalize_text(data.get("name_zh"))
        if not name_zh:
            raise PersonServiceError("中文姓名為必填。")

        person = Person(name_zh=name_zh, slug="", publish_status=PublishStatus.DRAFT)
        PersonService._assign_fields(person, data)

        if data.get("legacy_pending_detail"):
            person.legacy_pending_detail = True
        if data.get("legacy_id"):
            person.legacy_id = normalize_text(data.get("legacy_id"))

        # 先加入 session 取得 id，讓 ensure_unique_slug 的 exclude_id 正確。
        db.session.add(person)
        db.session.flush()

        PersonService._apply_slug(person, data.get("slug"), was_published=False)

        AuditLog.write(
            action=AuditAction.CREATE,
            entity_type="person",
            entity_id=person.id,
            summary=f"建立人物 {person.name_zh}（{person.slug}）",
            admin_user_id=admin_user_id,
            ip_address=ip_address,
        )

        PersonService._commit(f"create person {person.slug}")
        return person

    @staticmethod
    def update(
        person: Person,
        data: dict,
        admin_user_id: int | None = None,
        ip_address: str | None = None,
    ) -> Person:
        """更新人物。slug 變更且原本已公開時自動建立 301。"""
        was_published = person.publish_status == PublishStatus.PUBLISHED
        old_slug = person.slug

        PersonService._assign_fields(person, data)
        PersonService._apply_slug(person, data.get("slug"), was_published=was_published)

        summary = f"更新人物 {person.name_zh}（{person.slug}）"
        if old_slug != person.slug:
            summary += f"；slug {old_slug} -> {person.slug}"

        AuditLog.write(
            action=AuditAction.UPDATE,
            entity_type="person",
            entity_id=person.id,
            summary=summary,
            admin_user_id=admin_user_id,
            ip_address=ip_address,
        )

        PersonService._commit(f"update person {person.slug}")
        return person

    # ------------------------------------------------------------------
    # 狀態變更
    # ------------------------------------------------------------------
    @staticmethod
    def publish(
        person: Person, admin_user_id: int | None = None, ip_address: str | None = None
    ) -> Person:
        """發布人物。未通過發布門檻時拒絕（SAI §15.1）。

        Raises:
            PersonServiceError: 驗證未通過，訊息含所有阻擋原因。
        """
        result = PublishValidator.validate_person(person)
        if not result.is_valid:
            raise PersonServiceError(
                "無法發布，請先修正以下問題：\n- " + "\n- ".join(result.error_messages())
            )

        person.publish_status = PublishStatus.PUBLISHED

        AuditLog.write(
            action=AuditAction.PUBLISH,
            entity_type="person",
            entity_id=person.id,
            summary=f"發布人物 {person.name_zh}（{person.slug}）",
            admin_user_id=admin_user_id,
            ip_address=ip_address,
        )

        PersonService._commit(f"publish person {person.slug}")
        return person

    @staticmethod
    def unpublish(
        person: Person, admin_user_id: int | None = None, ip_address: str | None = None
    ) -> Person:
        """把人物退回草稿。"""
        person.publish_status = PublishStatus.DRAFT
        AuditLog.write(
            action=AuditAction.UPDATE,
            entity_type="person",
            entity_id=person.id,
            summary=f"取消發布人物 {person.name_zh}（{person.slug}）",
            admin_user_id=admin_user_id,
            ip_address=ip_address,
        )
        PersonService._commit(f"unpublish person {person.slug}")
        return person

    @staticmethod
    def archive(
        person: Person, admin_user_id: int | None = None, ip_address: str | None = None
    ) -> Person:
        """封存人物（SAI §7.4、§7.6：預設不硬刪）。"""
        person.publish_status = PublishStatus.ARCHIVED
        AuditLog.write(
            action=AuditAction.ARCHIVE,
            entity_type="person",
            entity_id=person.id,
            summary=f"封存人物 {person.name_zh}（{person.slug}）",
            admin_user_id=admin_user_id,
            ip_address=ip_address,
        )
        PersonService._commit(f"archive person {person.slug}")
        return person

    @staticmethod
    def graduate(
        person: Person,
        graduation_year: int | None,
        degree: str | None = None,
        thesis_title_zh: str | None = None,
        admin_user_id: int | None = None,
        ip_address: str | None = None,
    ) -> Person:
        """在學轉畢業（SAI §7.5、ADR-008、AC-06）。

        關鍵保證：
          - person.id 與 person.slug 完全不變。
          - 既有 ResearchOutput 關聯不被觸碰。
          - 不建立第二個 Person。
          - 不產生 redirect（個人 URL 仍然有效）。

        Raises:
            PersonServiceError: 人物並非在學狀態。

        為什麼要擋非 current 狀態：
          對 faculty 執行畢業轉換顯然是誤操作；對已是 alumni 的
          再執行一次會覆寫畢業資訊。明確拒絕比靜默執行安全。
        """
        if person.status != PersonStatus.CURRENT:
            raise PersonServiceError(
                f"只有在學成員可以轉為畢業生（目前狀態：{person.status}）。"
            )

        original_id = person.id
        original_slug = person.slug

        person.status = PersonStatus.ALUMNI

        year = validate_year(graduation_year)
        if year:
            person.graduation_year = year

        if degree and normalize_text(degree):
            person.degree = normalize_text(degree)

        # 論文題目「若已有可沿用」（SAI §7.5 步驟 2）：
        # 只有在傳入新值時才覆寫，避免清空既有資料。
        new_thesis = normalize_multiline(thesis_title_zh)
        if new_thesis:
            person.thesis_title_zh = new_thesis

        AuditLog.write(
            action=AuditAction.GRADUATE,
            entity_type="person",
            entity_id=person.id,
            summary=(
                f"{person.name_zh}（{person.slug}）由在學轉為畢業生"
                f"{f'，畢業年度 {person.graduation_year}' if person.graduation_year else ''}"
            ),
            admin_user_id=admin_user_id,
            ip_address=ip_address,
        )

        PersonService._commit(f"graduate person {person.slug}")

        # 事後保證（ADR-008 的可執行斷言）。若這裡失敗，
        # 表示有人在流程中改了 id/slug，是嚴重的資料完整性錯誤。
        assert person.id == original_id, "graduate 不得改變 person id（ADR-008）"
        assert person.slug == original_slug, "graduate 不得改變 person slug（AC-06）"

        return person

    # ------------------------------------------------------------------
    # 照片
    # ------------------------------------------------------------------
    @staticmethod
    def attach_photo(
        person: Person,
        file_storage,
        alt_zh: str | None = None,
        admin_user_id: int | None = None,
        ip_address: str | None = None,
    ) -> Person:
        """上傳並綁定人物照片。舊照片在成功後才刪除。

        為什麼「先上傳新的、成功後才刪舊的」：
          若先刪後傳，上傳失敗時人物會失去照片且無法復原。
          這個順序保證任何中途失敗都不會造成資料損失。
        """
        old_key = person.photo_path
        saved = MediaService.save_image(file_storage, purpose="people")

        person.photo_path = saved.key
        if alt_zh:
            person.photo_alt_zh = normalize_text(alt_zh)

        AuditLog.write(
            action=AuditAction.UPDATE,
            entity_type="person",
            entity_id=person.id,
            summary=f"更新人物照片 {person.name_zh}（{saved.key}）",
            admin_user_id=admin_user_id,
            ip_address=ip_address,
        )
        PersonService._commit(f"attach photo {person.slug}")

        # DB 已成功指向新檔，此時刪舊檔才安全。
        if old_key and old_key != saved.key:
            try:
                MediaService.delete_image(old_key)
            except Exception:  # noqa: BLE001 - 清理失敗不影響主要流程
                logger.warning("刪除舊照片失敗（已成為孤兒檔案）：%s", old_key)

        return person

    @staticmethod
    def remove_photo(
        person: Person, admin_user_id: int | None = None, ip_address: str | None = None
    ) -> Person:
        """移除人物照片（先解除引用再刪檔，SAI §16 Deletion）。"""
        old_key = person.photo_path
        if not old_key:
            return person

        person.photo_path = None
        person.photo_alt_zh = None
        person.photo_alt_en = None

        AuditLog.write(
            action=AuditAction.UPDATE,
            entity_type="person",
            entity_id=person.id,
            summary=f"移除人物照片 {person.name_zh}",
            admin_user_id=admin_user_id,
            ip_address=ip_address,
        )
        PersonService._commit(f"remove photo {person.slug}")

        try:
            MediaService.delete_image(old_key)
        except Exception:  # noqa: BLE001
            logger.warning("刪除照片檔案失敗：%s", old_key)

        return person
