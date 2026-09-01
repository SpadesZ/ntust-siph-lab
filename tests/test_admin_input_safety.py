# ============================================================
# NTUST SiPh Lab - 無效輸入不得破壞既有資料
#
# 上下游：
#   admin forms（SafeEmail / SafeUrl / IsoDate）
#       -> SettingsService._assign_normalized
#       -> 本測試（驗證「打錯字不會清空正確資料」）
#
# 檔案路徑：
#   tests/test_admin_input_safety.py
#
# 建立日期：2026-09-01
# 版本：v1.0
#
# 模組定位與責任邊界：
#   normalize_email / normalize_url 對「空輸入」與「格式錯誤」
#   都回傳 None。若直接指派，兩種情況都會清空欄位 ——
#   於是管理者把 Email 打錯一個字，就把研究室對外的聯絡信箱
#   整個清掉，而畫面仍顯示「已更新網站設定」。
#
#   本檔守住的性質：
#     1. 非空但格式錯誤 -> 不得覆寫既有值
#     2. 空輸入 -> 仍然可以正常清空（不能為了防呆而不能清空）
#     3. 表單層要先擋下來，讓使用者看得到欄位級錯誤
#
# 主要 Function：
#   test_invalid_email_does_not_wipe_existing_contact
#   test_empty_email_can_still_clear_the_field
#   test_invalid_url_does_not_wipe_existing_value
#   test_contact_email_form_rejects_invalid_address
#   test_publication_date_form_rejects_impossible_date
#   test_publication_date_accepts_valid_iso_date
#
# 依賴套件：pytest
#
# 資料庫使用方式：透過 conftest 的暫存 SQLite。
#
# 已知限制與禁止事項：
#   1. 禁止把「非空但無效」的處理改回靜默丟棄 ——
#      那正是本檔存在的原因。
#
# 驗證方式：
#   pytest tests/test_admin_input_safety.py
# ============================================================

from __future__ import annotations

import pytest


# ----------------------------------------------------------------------
# 1. Service 層：無效值不得覆寫既有資料
# ----------------------------------------------------------------------
def test_invalid_email_does_not_wipe_existing_contact(app):
    """打錯的 Email 不得清空原本正確的聯絡信箱。

    重現的是實測到的真實情境：把 yangcl@mail.ntust.edu.tw
    誤打成 yangcl(at)mail.ntust.edu.tw 送出後，
    原值被清空、前台 mailto 連結消失，而系統回報「成功」。
    """
    from app.models.site_setting import SiteSetting
    from app.services.settings_service import SettingsService, SettingsServiceError

    with app.app_context():
        SettingsService.update({"contact_email": "yangcl@mail.ntust.edu.tw"})
        assert SiteSetting.get().contact_email == "yangcl@mail.ntust.edu.tw"

        with pytest.raises(SettingsServiceError) as exc:
            SettingsService.update({"contact_email": "yangcl(at)mail.ntust.edu.tw"})

        assert "聯絡 Email" in str(exc.value), "錯誤訊息要指出是哪個欄位"

        assert SiteSetting.get().contact_email == "yangcl@mail.ntust.edu.tw", (
            "格式錯誤的輸入把原本正確的 Email 清掉了"
        )


def test_empty_email_can_still_clear_the_field(app):
    """空輸入仍然要能清空欄位（防呆不能變成不能清空）。"""
    from app.models.site_setting import SiteSetting
    from app.services.settings_service import SettingsService

    with app.app_context():
        SettingsService.update({"contact_email": "yangcl@mail.ntust.edu.tw"})
        SettingsService.update({"contact_email": "   "})

        assert SiteSetting.get().contact_email is None, "使用者清空欄位時應該真的清空"


@pytest.mark.parametrize(
    ("field", "good", "bad"),
    [
        ("map_url", "https://maps.example.edu/lab", "not a url at all"),
        ("join_cta_url", "https://example.edu/apply", "javascript:alert(1)"),
        ("official_ntust_url", "https://www.ntust.edu.tw/", "http://"),
    ],
)
def test_invalid_url_does_not_wipe_existing_value(app, field, good, bad):
    """無效網址不得覆寫既有網址（含危險 scheme）。"""
    from app.models.site_setting import SiteSetting
    from app.services.settings_service import SettingsService, SettingsServiceError

    with app.app_context():
        SettingsService.update({field: good})
        saved = getattr(SiteSetting.get(), field)
        assert saved, f"{field} 應先成功寫入"

        with pytest.raises(SettingsServiceError):
            SettingsService.update({field: bad})

        assert getattr(SiteSetting.get(), field) == saved, (
            f"{field} 的既有值被無效輸入清掉了"
        )


# ----------------------------------------------------------------------
# 2. 表單層：使用者要看得到欄位級錯誤
# ----------------------------------------------------------------------
def test_contact_email_form_rejects_invalid_address(app):
    """設定表單必須擋下格式錯誤的 Email 並指到該欄位。"""
    from app.blueprints.admin.forms import SiteSettingForm

    with app.test_request_context(
        method="POST",
        data={
            "lab_name_zh": "測試研究室",
            "lab_name_en": "Test Lab",
            "contact_email": "yangcl(at)mail.ntust.edu.tw",
        },
    ):
        form = SiteSettingForm()
        assert not form.validate(), "格式錯誤的 Email 不該通過表單驗證"
        assert form.contact_email.errors, "錯誤必須掛在 contact_email 欄位上"


@pytest.mark.parametrize("bad_date", ["2025-13-45", "abcdefghij", "2025/03/14", "2025-02-30"])
def test_publication_date_form_rejects_impossible_date(app, bad_date):
    """出版日期必須是真實存在的 YYYY-MM-DD。

    2025-02-30 是形狀正確但不存在的日期，原本會通過
    Length(max=10) 然後被靜默轉成 None。
    """
    from app.blueprints.admin.forms import ResearchForm

    with app.test_request_context(
        method="POST",
        data={"output_type": "journal", "year": "2025", "publication_date": bad_date},
    ):
        form = ResearchForm()
        form.people.choices = []
        assert not form.validate(), f"{bad_date} 不該通過驗證"
        assert form.publication_date.errors, "錯誤必須掛在 publication_date 欄位上"


def test_publication_date_accepts_valid_iso_date(app):
    """合法日期仍要能通過（不能防呆過頭）。"""
    from app.blueprints.admin.forms import ResearchForm

    with app.test_request_context(
        method="POST",
        data={"output_type": "journal", "year": "2025", "publication_date": "2025-03-14"},
    ):
        form = ResearchForm()
        form.people.choices = []
        assert form.validate(), f"合法日期不該被擋：{form.errors}"
