# ============================================================
# NTUST SiPh Lab - Site Settings Service
#
# 上下游：
#   Admin /admin/settings -> SiteSettingForm -> SettingsService.update()
#       -> SiteSetting -> DB -> AuditLog
#   SettingsService -> MediaService（logo / hero / OG 圖片）
#   SiteSetting -> context processor -> 所有 Template
#
# 檔案路徑：
#   app/services/settings_service.py
#
# 建立日期：2026-08-14
# 最後重大修改：2026-08-14
# 版本：v1.0
#
# 模組定位與責任邊界：
#   全站設定的寫入邊界（SAI §8.6、§15.4）。使用者需求明訂
#   「所有網站主要內容均須可由後台維護」，本服務即是那條路徑的
#   終點。
#
#   責任邊界（不得做的事）：
#     - 不得寫入 secret（SAI §8.9 明確禁止）。
#     - 不得 render HTML。
#     - 不得直接被前台呼叫（前台只讀 SiteSetting.get()）。
#
# 輸入 -> 處理 -> 輸出 Pipeline：
#   欄位 dict -> 正規化 -> 寫入 singleton -> AuditLog -> commit
#   research_focus / lab_proof / social_links 為結構化 JSON，
#   由 route 解析表單的重複欄位後以 list[dict] 傳入。
#
# 主要 Class / Function：
#   SettingsServiceError
#   SettingsService.update(data, ...)
#   SettingsService.update_media(field, file_storage, ...)
#   SettingsService.remove_media(field, ...)
#
# 依賴套件：
#   app.models.site_setting、app.utils.validators、app.services.media_service
#
# 環境變數：無。
#
# 資料庫使用方式：
#   site_settings（單列）、audit_logs。單一 transaction。
#
# Error Handling / Fallback：
#   SettingsServiceError 訊息可直接顯示給管理員。
#   任何例外都 rollback。
#
# 特殊機制（media 欄位白名單）：
#   update_media 以 _MEDIA_FIELDS 限制可寫入的欄位名稱。
#   為什麼：欄位名稱來自表單輸入，若直接 setattr 會形成
#   任意屬性寫入漏洞（攻擊者可覆寫 llms_txt_enabled 等設定）。
#   白名單讓可寫範圍是封閉集合。
#
# 已知限制與禁止事項：
#   1. 禁止透過本服務修改 production_base_url 來改變 canonical；
#      canonical 一律以 PUBLIC_BASE_URL 環境變數為準
#      （見 seo_service.absolute_url 的說明）。
#      本欄位僅作管理者可見的紀錄。
#   2. 禁止把 llms_txt_enabled 描述成排名開關（SAI §13.2）。
#
# 維護契約：
#   SiteSetting 新增欄位時，必須同步更新本服務的 _assign_fields
#   與 admin 表單/模板，否則該欄位無法由後台維護。
#
# 驗證方式：
#   pytest tests/test_settings.py
# ============================================================

from __future__ import annotations

import logging

from app.extensions import db
from app.models.audit_log import AuditLog
from app.models.mixins import AuditAction
from app.models.site_setting import SiteSetting
from app.services.media_service import MediaService
from app.utils.validators import (
    normalize_email,
    normalize_multiline,
    normalize_text,
    normalize_url,
)

logger = logging.getLogger(__name__)

#: 允許透過 update_media 寫入的欄位（見檔頭「特殊機制」）。
_MEDIA_FIELDS = {
    "logo_path": "site",
    "hero_media_path": "site",
    "og_image_path": "site",
}


class SettingsServiceError(RuntimeError):
    """設定更新失敗；訊息可直接顯示給管理員。"""


class SettingsService:
    """全站設定寫入（SAI §8.6、§15.4）。"""

    @staticmethod
    def _assign_normalized(
        setting: SiteSetting,
        data: dict,
        errors: list[str],
        field: str,
        label: str,
        normalizer,
    ) -> None:
        """正規化後指派；輸入非空卻無法正規化時「不覆寫」並記錄錯誤。

        為什麼需要區分兩種「結果是 None」：
          normalize_email / normalize_url 對「空輸入」與「格式錯誤」
          都回傳 None。直接指派會讓這兩種情況都清空欄位 ——
          於是管理者把 Email 打錯一個字，就把研究室對外的
          聯絡信箱整個清掉，而系統還回報「已更新」。

          空輸入  -> 使用者確實想清空，指派 None（合法）。
          格式錯  -> 保留原值並回報錯誤，絕不覆寫。

        表單層（SafeEmail / SafeUrl）已經會先擋下來，這裡是
        第二道防線：seed script 與 CLI 不經過表單，仍需保護。

        欄位完全不在 data 裡時「不碰它」：呼叫端沒有提到這個欄位，
        與「呼叫端要求清空」是兩件事。少了這個區分，
        任何從表單移除的欄位都會在每次儲存時被清成 None。
        """
        if field not in data:
            return

        raw = data.get(field)

        if normalize_text(raw) is None:
            setattr(setting, field, None)
            return

        value = normalizer(raw)
        if value is None:
            errors.append(f"「{label}」格式不正確（{str(raw).strip()}），已保留原本的值。")
            return

        setattr(setting, field, value)

    @staticmethod
    def _assign_fields(setting: SiteSetting, data: dict) -> list[str]:
        """把表單資料寫入設定欄位（逐 tab 對應 SAI §15.4）。

        Returns:
            notices —— 已儲存，但有事情必須告知使用者
            （例如某一列因缺必要欄位而未被寫入）。

        Raises:
            SettingsServiceError: 有欄位「非空但格式不合法」。
                寧可整筆拒絕也不要靜默覆寫既有正確資料。
        """
        errors: list[str] = []
        notices: list[str] = []

        # --- Lab Identity ---
        lab_name_zh = normalize_text(data.get("lab_name_zh"))
        if lab_name_zh:
            setting.lab_name_zh = lab_name_zh
        lab_name_en = normalize_text(data.get("lab_name_en"))
        if lab_name_en:
            setting.lab_name_en = lab_name_en

        # short_name 已不由後台表單維護（全專案沒有任何地方讀取它）。
        # 只在呼叫端明確傳入時才寫入，避免每次儲存把既有資料清空。
        if "short_name" in data:
            setting.short_name = normalize_text(data.get("short_name"))
        setting.department_zh = normalize_text(data.get("department_zh"))
        setting.department_en = normalize_text(data.get("department_en"))
        setting.university_zh = normalize_text(data.get("university_zh"))
        setting.university_en = normalize_text(data.get("university_en"))

        # --- Homepage ---
        setting.hero_title_zh = normalize_multiline(data.get("hero_title_zh"))
        setting.hero_title_en = normalize_multiline(data.get("hero_title_en"))
        setting.hero_intro_zh = normalize_multiline(data.get("hero_intro_zh"))
        setting.hero_intro_en = normalize_multiline(data.get("hero_intro_en"))
        setting.hero_media_alt_zh = normalize_text(data.get("hero_media_alt_zh"))

        # 結構化區塊：由 route 解析為 list[dict] 後傳入。
        # 被略過的列會產生 notice，讓使用者知道「哪一列、為什麼沒存」。
        if "research_focus" in data:
            setting.research_focus, focus_notes = SettingsService._clean_focus(
                data.get("research_focus")
            )
            notices.extend(focus_notes)
        if "lab_proof" in data:
            setting.lab_proof, proof_notes = SettingsService._clean_proof(
                data.get("lab_proof")
            )
            notices.extend(proof_notes)
        if "social_links" in data:
            setting.social_links, link_notes = SettingsService._clean_links(
                data.get("social_links")
            )
            notices.extend(link_notes)

        # --- About ---
        setting.about_intro_zh = normalize_multiline(data.get("about_intro_zh"))
        setting.about_intro_en = normalize_multiline(data.get("about_intro_en"))
        setting.about_methods_zh = normalize_multiline(data.get("about_methods_zh"))

        # --- Join & Contact ---
        # Email 與 URL 一律走 _assign_normalized：它們是唯一
        # 「正規化失敗會回 None」的欄位，也就是會靜默清空資料的來源。
        SettingsService._assign_normalized(
            setting, data, errors, "contact_email", "聯絡 Email", normalize_email
        )
        setting.address_zh = normalize_multiline(data.get("address_zh"))
        setting.address_en = normalize_multiline(data.get("address_en"))
        SettingsService._assign_normalized(
            setting, data, errors, "map_url", "地圖連結", normalize_url
        )
        setting.join_title_zh = normalize_text(data.get("join_title_zh"))
        setting.join_body_zh = normalize_multiline(data.get("join_body_zh"))
        setting.join_cta_label_zh = normalize_text(data.get("join_cta_label_zh"))
        SettingsService._assign_normalized(
            setting, data, errors, "join_cta_url", "招募按鈕連結", normalize_url
        )

        # --- SEO defaults ---
        setting.default_title_suffix = normalize_text(data.get("default_title_suffix"))
        setting.default_description_zh = normalize_text(data.get("default_description_zh"))
        SettingsService._assign_normalized(
            setting, data, errors, "production_base_url", "正式網域紀錄", normalize_url
        )

        # --- External identity ---
        SettingsService._assign_normalized(
            setting, data, errors, "official_ntust_url", "NTUST 官方頁連結", normalize_url
        )

        # --- Advanced ---
        if "llms_txt_enabled" in data:
            setting.llms_txt_enabled = bool(data.get("llms_txt_enabled"))

        if errors:
            raise SettingsServiceError(" ".join(errors))

        return notices

    #: 多值列的清理結果。
    #:
    #: 為什麼要回報而非靜默丟棄：
    #:   原本三個 _clean_* 都是「不合格就 continue」，整列直接消失。
    #:   管理者填了「顯示名稱」卻忘了填網址、或網址打錯，
    #:   存檔後只看到那一列變空白，系統還回報「已更新網站設定」。
    #:   這與 forms.SafeUrl docstring 指出的是同一種缺陷 ——
    #:   當時只修了有宣告 validator 的欄位，多值列走
    #:   parse_repeated 完全繞過驗證，因此漏掉。
    #:
    #: 「整列皆空」仍然靜默略過：那是畫面上刻意保留的空白列，
    #: 不是使用者的錯誤。
    @staticmethod
    def _clean_focus(items) -> tuple[list[dict], list[str]]:
        """清理首頁研究主題清單。

        description 允許為空 —— 母站僅提供六項專長名稱，
        依 SAI §2.3 不得由 Agent 補寫定義（見 site_setting.py）。

        Returns:
            (清理後清單, 需要回報給使用者的訊息)
        """
        cleaned: list[dict] = []
        notices: list[str] = []

        for index, item in enumerate(items or [], start=1):
            if not isinstance(item, dict):
                continue

            title_zh = normalize_text(item.get("title_zh"))
            title_en = normalize_text(item.get("title_en"))
            description = normalize_multiline(item.get("description_zh"))

            if not title_zh:
                if title_en or description:
                    notices.append(
                        f"研究方向第 {index} 列缺少「主題名稱（中）」，該列未儲存。"
                    )
                continue

            cleaned.append(
                {
                    "title_zh": title_zh,
                    "title_en": title_en or "",
                    "description_zh": description or "",
                }
            )
        return cleaned, notices

    @staticmethod
    def _clean_proof(items) -> tuple[list[dict], list[str]]:
        """清理首頁可驗證事實清單（SAI §6.3 禁止虛構數據）。"""
        cleaned: list[dict] = []
        notices: list[str] = []

        for index, item in enumerate(items or [], start=1):
            if not isinstance(item, dict):
                continue

            label = normalize_text(item.get("label_zh"))
            value = normalize_text(item.get("value_zh"))
            raw_source = normalize_text(item.get("source"))

            if not label or not value:
                if label or value or raw_source:
                    missing = "項目名稱" if not label else "內容"
                    notices.append(
                        f"研究室事實第 {index} 列缺少「{missing}」，該列未儲存。"
                    )
                continue

            source = normalize_url(raw_source) if raw_source else None
            if raw_source and source is None:
                notices.append(
                    f"研究室事實第 {index} 列的來源網址格式不正確（{raw_source}），"
                    "該列已儲存但來源留空。"
                )

            cleaned.append(
                {"label_zh": label, "value_zh": value, "source": source or ""}
            )
        return cleaned, notices

    @staticmethod
    def _clean_links(items) -> tuple[list[dict], list[str]]:
        """清理外部連結清單（footer 與 Organization.sameAs 共用）。"""
        cleaned: list[dict] = []
        notices: list[str] = []

        for index, item in enumerate(items or [], start=1):
            if not isinstance(item, dict):
                continue

            label = normalize_text(item.get("label"))
            raw_url = normalize_text(item.get("url"))

            if not raw_url:
                if label:
                    notices.append(
                        f"外部連結第 {index} 列填了顯示名稱「{label}」但沒有網址，該列未儲存。"
                    )
                continue

            url = normalize_url(raw_url)
            if not url:
                notices.append(
                    f"外部連結第 {index} 列的網址格式不正確（{raw_url}），該列未儲存。"
                )
                continue

            cleaned.append({"label": label or url, "url": url})
        return cleaned, notices

    # ------------------------------------------------------------------
    # 公開介面
    # ------------------------------------------------------------------
    @staticmethod
    def update(
        data: dict,
        admin_user_id: int | None = None,
        ip_address: str | None = None,
        notices: list[str] | None = None,
    ) -> SiteSetting:
        """更新全站設定。

        Args:
            notices: 若傳入 list，會把「已儲存但需要告知使用者的事」
                附加進去 —— 例如某一列因缺必要欄位而未被寫入。

                用 out-parameter 而不是改變回傳型別，是因為
                seed script 與既有測試都以 `setting = update(...)`
                的形式呼叫；改回傳 tuple 會波及十餘個呼叫點，
                而它們並不需要這些訊息。
        """
        setting = SiteSetting.get()

        try:
            collected = SettingsService._assign_fields(setting, data)
        except SettingsServiceError:
            # _assign_fields 在發現問題前可能已寫入部分欄位；
            # rollback 確保「整筆拒絕」而不是留下半套狀態。
            db.session.rollback()
            raise

        if notices is not None:
            notices.extend(collected)

        AuditLog.write(
            action=AuditAction.UPDATE,
            entity_type="site_setting",
            entity_id=setting.id,
            summary="更新網站設定",
            admin_user_id=admin_user_id,
            ip_address=ip_address,
        )

        try:
            db.session.commit()
        except Exception as exc:  # noqa: BLE001
            db.session.rollback()
            logger.exception("網站設定更新失敗")
            raise SettingsServiceError(f"設定儲存失敗：{exc}") from exc

        return setting

    @staticmethod
    def update_media(
        field: str,
        file_storage,
        admin_user_id: int | None = None,
        ip_address: str | None = None,
    ) -> SiteSetting:
        """更新設定中的圖片欄位（logo / hero / OG）。

        Raises:
            SettingsServiceError: 欄位不在白名單內。
        """
        if field not in _MEDIA_FIELDS:
            raise SettingsServiceError(f"不允許的媒體欄位：{field}")

        setting = SiteSetting.get()
        old_key = getattr(setting, field)

        saved = MediaService.save_image(file_storage, purpose=_MEDIA_FIELDS[field])
        setattr(setting, field, saved.key)

        AuditLog.write(
            action=AuditAction.UPDATE,
            entity_type="site_setting",
            entity_id=setting.id,
            summary=f"更新網站圖片 {field}（{saved.key}）",
            admin_user_id=admin_user_id,
            ip_address=ip_address,
        )

        try:
            db.session.commit()
        except Exception as exc:  # noqa: BLE001
            db.session.rollback()
            raise SettingsServiceError(f"圖片儲存失敗：{exc}") from exc

        if old_key and old_key != saved.key:
            try:
                MediaService.delete_image(old_key)
            except Exception:  # noqa: BLE001
                logger.warning("刪除舊網站圖片失敗：%s", old_key)

        return setting

    @staticmethod
    def remove_media(
        field: str, admin_user_id: int | None = None, ip_address: str | None = None
    ) -> SiteSetting:
        """移除設定中的圖片欄位。"""
        if field not in _MEDIA_FIELDS:
            raise SettingsServiceError(f"不允許的媒體欄位：{field}")

        setting = SiteSetting.get()
        old_key = getattr(setting, field)
        if not old_key:
            return setting

        setattr(setting, field, None)

        AuditLog.write(
            action=AuditAction.UPDATE,
            entity_type="site_setting",
            entity_id=setting.id,
            summary=f"移除網站圖片 {field}",
            admin_user_id=admin_user_id,
            ip_address=ip_address,
        )

        try:
            db.session.commit()
        except Exception as exc:  # noqa: BLE001
            db.session.rollback()
            raise SettingsServiceError(f"移除圖片失敗：{exc}") from exc

        try:
            MediaService.delete_image(old_key)
        except Exception:  # noqa: BLE001
            logger.warning("刪除網站圖片檔案失敗：%s", old_key)

        return setting
