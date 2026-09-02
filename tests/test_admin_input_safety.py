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
#   本檔守住的性質（統一主題：使用者的資料不得無聲消失）：
#     1. 非空但格式錯誤 -> 不得覆寫既有值
#     2. 空輸入 -> 仍然可以正常清空（不能為了防呆而不能清空）
#     3. 表單層要先擋下來，讓使用者看得到欄位級錯誤
#     4. 驗證失敗重新渲染時 -> 不得把使用者剛填的多值列還原掉
#     5. 多值列被略過時 -> 必須說明「哪一列、為什麼」，不得無聲丟棄
#
# 主要 Function：
#   test_invalid_email_does_not_wipe_existing_contact
#   test_empty_email_can_still_clear_the_field
#   test_invalid_url_does_not_wipe_existing_value
#   test_contact_email_form_rejects_invalid_address
#   test_publication_date_form_rejects_impossible_date
#   test_publication_date_accepts_valid_iso_date
#   test_validation_failure_preserves_repeat_rows
#   test_get_settings_shows_stored_repeat_rows
#   test_dropped_link_row_is_reported
#   test_dropped_focus_row_is_reported
#   test_fully_blank_rows_are_silently_ignored
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
        assert form.validate(), f"合法日期不該被擋：{form.errors}"


# ----------------------------------------------------------------------
# 3. 驗證失敗重新渲染時，不得丟掉使用者剛填的多值列
# ----------------------------------------------------------------------
#: 使用者剛輸入、尚未成功儲存的內容。
_TYPED_DESCRIPTION = "利用矽基光波導進行環境與生醫訊號的即時感測"
_TYPED_PROOF_LABEL = "合作單位"
_TYPED_LINK_LABEL = "研究室 GitHub"


def _settings_post_data(*, lab_name_zh: str) -> dict:
    """組出一份帶有多值列的設定表單資料。"""
    return {
        "lab_name_zh": lab_name_zh,
        "lab_name_en": "Test Lab",
        "research_focus-title_zh": "光電感測技術",
        "research_focus-title_en": "Optical Sensing",
        "research_focus-description_zh": _TYPED_DESCRIPTION,
        "lab_proof-label_zh": _TYPED_PROOF_LABEL,
        "lab_proof-value_zh": "中央研究院應用科學研究中心",
        "lab_proof-source": "",
        "social_links-label": _TYPED_LINK_LABEL,
        "social_links-url": "https://github.com/example",
    }


def test_validation_failure_preserves_repeat_rows(logged_in_client):
    """驗證失敗時，多值欄位的編輯不得被還原成資料庫舊值。

    觸發方式刻意選用「必填欄位填成空白字元」：
    瀏覽器的 HTML5 required 認為有填而放行，
    伺服器端 DataRequired 會 strip 後判定失敗 ——
    這是真實使用者最容易遇到的驗證失敗路徑之一。
    """
    response = logged_in_client.post(
        "/admin/settings",
        data=_settings_post_data(lab_name_zh="   "),
        follow_redirects=False,
    )

    assert response.status_code == 200, "驗證失敗應重新渲染表單而非轉址"
    html = response.get_data(as_text=True)

    for typed in (_TYPED_DESCRIPTION, _TYPED_PROOF_LABEL, _TYPED_LINK_LABEL):
        assert typed in html, (
            f"驗證失敗後「{typed}」從畫面上消失了 —— "
            "使用者剛輸入的多值列被資料庫舊值覆蓋，且沒有任何提示"
        )


def test_dropped_link_row_is_reported(app):
    """外部連結只填名稱沒填網址時，必須告知該列未儲存。

    原本 _clean_links 直接 continue，整列消失且畫面回報「已更新」。
    """
    from app.services.settings_service import SettingsService

    with app.app_context():
        notices: list[str] = []
        SettingsService.update(
            {"social_links": [{"label": "研究室 Facebook", "url": ""}]}, notices=notices
        )

        assert notices, "被略過的列必須產生提示，不得無聲丟棄"
        assert any("外部連結" in n and "研究室 Facebook" in n for n in notices), notices


def test_dropped_focus_row_is_reported(app):
    """研究方向填了說明卻沒填主題名稱時，必須告知該列未儲存。"""
    from app.services.settings_service import SettingsService

    with app.app_context():
        notices: list[str] = []
        SettingsService.update(
            {"research_focus": [{"title_zh": "", "description_zh": "只填了說明"}]},
            notices=notices,
        )

        assert any("研究方向" in n for n in notices), notices


def test_fully_blank_rows_are_silently_ignored(app):
    """整列皆空是畫面上刻意保留的空白列，不該產生噪音提示。"""
    from app.services.settings_service import SettingsService

    with app.app_context():
        notices: list[str] = []
        SettingsService.update(
            {
                "research_focus": [{"title_zh": "", "title_en": "", "description_zh": ""}],
                "social_links": [{"label": "", "url": ""}],
                "lab_proof": [{"label_zh": "", "value_zh": "", "source": ""}],
            },
            notices=notices,
        )

        assert notices == [], f"空白列不該產生提示：{notices}"


# ----------------------------------------------------------------------
# 4. 從表單移除的欄位，不得在儲存時被清空
# ----------------------------------------------------------------------
#: 已從後台表單移除但仍保留 DB 欄位與資料的項目。
#: 移除表單欄位後，to_dict() 不再帶這個 key；若 service 仍然
#: 無條件 `setattr(obj, field, normalize(data.get(field)))`，
#: 每次儲存都會把既有資料清成 None —— 使用者甚至看不到欄位，
#: 完全無從察覺資料正在流失。
def test_settings_save_does_not_wipe_removed_fields(app):
    """儲存設定不得清掉已從表單移除的 short_name / production_base_url。"""
    from app.models.site_setting import SiteSetting
    from app.services.settings_service import SettingsService

    with app.app_context():
        # 以「明確傳入」的方式建立既有資料（seed / CLI 的用法）
        SettingsService.update(
            {"short_name": "SiPh Lab", "production_base_url": "https://example.edu"}
        )
        assert SiteSetting.get().short_name == "SiPh Lab"

        # 模擬後台表單儲存：payload 不含這兩個 key
        SettingsService.update({"lab_name_zh": "測試研究室", "lab_name_en": "Test Lab"})

        setting = SiteSetting.get()
        assert setting.short_name == "SiPh Lab", "表單儲存把已移除欄位的資料清掉了"
        assert setting.production_base_url == "https://example.edu"


def test_save_does_not_wipe_removed_seo_overrides(app):
    """儲存不得清掉已從表單移除的 SEO 覆寫欄位。

    這兩個欄位在實際資料中 14 筆全空，因此從表單移除；
    但若哪天有人用 CLI 或 seed 寫入，後台的一次儲存
    不該把它悄悄清掉 —— 使用者甚至看不到欄位。
    """
    from app.extensions import db
    from app.models.person import Person
    from app.services.person_service import PersonService

    with app.app_context():
        person = PersonService.create(
            {
                "name_zh": "SEO 保留測試",
                "status": "current",
                "research_focus_zh": "x",
                "seo_title_zh": "手寫標題",
                "seo_description_zh": "手寫描述",
            }
        )
        person_id = person.id
        assert person.seo_title_zh == "手寫標題"

        # 模擬後台表單儲存：payload 不含這兩個 key
        PersonService.update(person, {"name_zh": "SEO 保留測試", "status": "current"})

        reloaded = db.session.get(Person, person_id)
        assert reloaded.seo_title_zh == "手寫標題", "表單儲存把 SEO 覆寫清掉了"
        assert reloaded.seo_description_zh == "手寫描述"


def test_person_save_does_not_wipe_removed_alt_en(app):
    """儲存人物不得清掉已從表單移除的 photo_alt_en。"""
    from app.models.person import Person
    from app.services.person_service import PersonService

    with app.app_context():
        person = PersonService.create(
            {
                "name_zh": "欄位保留測試",
                "status": "current",
                "research_focus_zh": "x",
                "photo_alt_en": "Portrait of the researcher",
            }
        )
        person_id = person.id
        assert person.photo_alt_en == "Portrait of the researcher"

        # 模擬後台表單儲存：payload 不含 photo_alt_en
        PersonService.update(person, {"name_zh": "欄位保留測試", "status": "current"})

        from app.extensions import db

        assert db.session.get(Person, person_id).photo_alt_en == (
            "Portrait of the researcher"
        ), "表單儲存把已移除欄位的資料清掉了"


def test_get_settings_shows_stored_repeat_rows(logged_in_client, app):
    """GET 時仍要顯示資料庫既有的多值資料（不能為了修 POST 而弄壞 GET）。"""
    from app.services.settings_service import SettingsService

    with app.app_context():
        SettingsService.update(
            {
                "lab_name_zh": "測試研究室",
                "lab_name_en": "Test Lab",
                "research_focus": [{"title_zh": "矽光子技術", "description_zh": "已儲存的說明"}],
            }
        )

    html = logged_in_client.get("/admin/settings").get_data(as_text=True)

    assert "矽光子技術" in html
    assert "已儲存的說明" in html
