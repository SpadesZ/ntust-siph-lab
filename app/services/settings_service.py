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
    def _assign_fields(setting: SiteSetting, data: dict) -> None:
        """把表單資料寫入設定欄位（逐 tab 對應 SAI §15.4）。"""

        # --- Lab Identity ---
        lab_name_zh = normalize_text(data.get("lab_name_zh"))
        if lab_name_zh:
            setting.lab_name_zh = lab_name_zh
        lab_name_en = normalize_text(data.get("lab_name_en"))
        if lab_name_en:
            setting.lab_name_en = lab_name_en

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
        if "research_focus" in data:
            setting.research_focus = SettingsService._clean_focus(data.get("research_focus"))
        if "lab_proof" in data:
            setting.lab_proof = SettingsService._clean_proof(data.get("lab_proof"))
        if "social_links" in data:
            setting.social_links = SettingsService._clean_links(data.get("social_links"))

        # --- About ---
        setting.about_intro_zh = normalize_multiline(data.get("about_intro_zh"))
        setting.about_methods_zh = normalize_multiline(data.get("about_methods_zh"))

        # --- Join & Contact ---
        setting.contact_email = normalize_email(data.get("contact_email"))
        setting.address_zh = normalize_multiline(data.get("address_zh"))
        setting.address_en = normalize_multiline(data.get("address_en"))
        setting.map_url = normalize_url(data.get("map_url"))
        setting.join_title_zh = normalize_text(data.get("join_title_zh"))
        setting.join_body_zh = normalize_multiline(data.get("join_body_zh"))
        setting.join_cta_label_zh = normalize_text(data.get("join_cta_label_zh"))
        setting.join_cta_url = normalize_url(data.get("join_cta_url"))

        # --- SEO defaults ---
        setting.default_title_suffix = normalize_text(data.get("default_title_suffix"))
        setting.default_description_zh = normalize_text(data.get("default_description_zh"))
        setting.production_base_url = normalize_url(data.get("production_base_url"))

        # --- External identity ---
        setting.official_ntust_url = normalize_url(data.get("official_ntust_url"))

        # --- Advanced ---
        if "llms_txt_enabled" in data:
            setting.llms_txt_enabled = bool(data.get("llms_txt_enabled"))

    @staticmethod
    def _clean_focus(items) -> list[dict]:
        """清理首頁研究主題清單。

        description 允許為空 —— 母站僅提供六項專長名稱，
        依 SAI §2.3 不得由 Agent 補寫定義（見 site_setting.py）。
        """
        cleaned: list[dict] = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            title_zh = normalize_text(item.get("title_zh"))
            if not title_zh:
                continue  # 沒有標題的項目無意義，直接略過
            cleaned.append(
                {
                    "title_zh": title_zh,
                    "title_en": normalize_text(item.get("title_en")) or "",
                    "description_zh": normalize_multiline(item.get("description_zh")) or "",
                }
            )
        return cleaned

    @staticmethod
    def _clean_proof(items) -> list[dict]:
        """清理首頁可驗證事實清單（SAI §6.3 禁止虛構數據）。"""
        cleaned: list[dict] = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            label = normalize_text(item.get("label_zh"))
            value = normalize_text(item.get("value_zh"))
            if not label or not value:
                continue
            cleaned.append(
                {
                    "label_zh": label,
                    "value_zh": value,
                    "source": normalize_url(item.get("source")) or "",
                }
            )
        return cleaned

    @staticmethod
    def _clean_links(items) -> list[dict]:
        """清理外部連結清單（footer 與 Organization.sameAs 共用）。"""
        cleaned: list[dict] = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            url = normalize_url(item.get("url"))
            if not url:
                continue
            cleaned.append(
                {"label": normalize_text(item.get("label")) or url, "url": url}
            )
        return cleaned

    # ------------------------------------------------------------------
    # 公開介面
    # ------------------------------------------------------------------
    @staticmethod
    def update(
        data: dict, admin_user_id: int | None = None, ip_address: str | None = None
    ) -> SiteSetting:
        """更新全站設定。"""
        setting = SiteSetting.get()
        SettingsService._assign_fields(setting, data)

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
