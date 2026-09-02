# ============================================================
# NTUST SiPh Lab - Admin 表單結構完整性測試
#
# 上下游：
#   templates/admin/_macros.html::confirm_button
#       -> person_form.html / research_form.html / settings.html
#       -> 本測試（驗證產生的 HTML 結構在瀏覽器中仍然可用）
#
# 檔案路徑：
#   tests/test_admin_form_integrity.py
#
# 建立日期：2026-09-01
# 版本：v1.0
#
# 模組定位與責任邊界：
#   既有的 test_admin.py::test_admin_pages_render 只驗證頁面
#   回應 200。但「頁面成功渲染」與「表單真的送得出去」是兩件事：
#   一旦 <form> 巢狀，頁面依然回 200、按鈕依然看得見，
#   點下去卻完全沒有請求送出。本檔專門守住這個落差。
#
#   責任邊界（不得做的事）：
#     - 不驗證欄位內容或業務規則（那是 test_admin_forms.py）。
#     - 不驗證可及性（那是 test_a11y.py）。
#
# 為什麼不用 BeautifulSoup 判斷巢狀：
#   BeautifulSoup 的 html.parser 允許 <form> 巢狀，會回報出
#   「原始碼寫了什麼」而非「瀏覽器會怎麼解讀」。真實瀏覽器
#   遵循 HTML5 parsing spec：內層 <form> 的開始標籤被丟棄，
#   而 </form> 結束標籤照常生效 —— 於是內層的 </form>
#   會提前關閉外層主表單。本檔以 _main_form_html() 明確
#   複製該行為，才能反映管理者實際遇到的狀況。
#
# 主要 Function：
#   test_person_form_has_no_nested_form
#   test_research_form_has_no_nested_form
#   test_settings_form_has_no_nested_form
#   test_person_form_save_button_is_inside_main_form
#   test_person_form_keeps_all_fields_inside_main_form
#   test_person_photo_delete_has_its_own_form
#
# 依賴套件：pytest, flask test client
#
# 資料庫使用方式：
#   透過 conftest 的 app / logged_in_client fixture，使用暫存 SQLite。
#
# 已知限制與禁止事項：
#   1. 禁止把本檔的斷言放寬成「只檢查回應 200」——
#      那正是原本漏掉這個缺陷的原因。
#
# 維護契約：
#   任何新的 admin 表單頁若含有「圖片預覽 + 移除按鈕」，
#   都必須加入本檔的參數化清單，否則同樣的缺陷會再次出現。
#
# 驗證方式：
#   pytest tests/test_admin_form_integrity.py
# ============================================================

from __future__ import annotations

import io

import pytest

#: 主表單在模板中的識別 class。
_MAIN_FORM_MARKER = 'class="admin-form"'


def _main_form_html(html: str) -> str:
    """取出「瀏覽器認定的主表單範圍」。

    見檔頭「為什麼不用 BeautifulSoup 判斷巢狀」：
    第一個 </form> 就是主表單在瀏覽器眼中的結束位置。
    """
    start = html.find(_MAIN_FORM_MARKER)
    assert start != -1, "頁面找不到 admin-form 主表單"

    end = html.find("</form>", start)
    assert end != -1, "主表單沒有結束標籤"

    return html[start:end]


def _get_html(client, path: str) -> str:
    response = client.get(path)
    assert response.status_code == 200, f"{path} 應該正常渲染，實際 {response.status_code}"
    return response.get_data(as_text=True)


@pytest.fixture()
def person_with_photo(app, png_bytes):
    """建立一位「已上傳照片」的成員。

    照片是觸發條件：沒有照片時模板不會渲染
    media-preview 區塊，也就不會產生巢狀 <form>。
    """
    from werkzeug.datastructures import FileStorage

    from app.services.person_service import PersonService

    with app.app_context():
        person = PersonService.create(
            {
                "name_zh": "表單結構測試",
                "status": "current",
                "research_focus_zh": "測試研究方向",
            }
        )
        PersonService.attach_photo(
            person,
            FileStorage(stream=io.BytesIO(png_bytes), filename="p.png", content_type="image/png"),
            alt_zh="表單結構測試的照片",
        )
        return {"id": person.id}


@pytest.fixture()
def output_with_image(app, png_bytes):
    """建立一筆「已上傳主圖」的研究成果。"""
    from werkzeug.datastructures import FileStorage

    from app.services.research_service import ResearchService

    with app.app_context():
        output = ResearchService.create(
            {
                "output_type": "journal",
                "year": 2026,
                "title_zh": "表單結構測試成果",
            }
        )
        ResearchService.attach_hero_image(
            output,
            FileStorage(stream=io.BytesIO(png_bytes), filename="h.png", content_type="image/png"),
            alt_zh="表單結構測試的主圖",
        )
        return {"id": output.id}


@pytest.fixture()
def settings_with_logo(app, png_bytes):
    """讓網站設定帶有 logo（同樣是巢狀 form 的觸發條件）。"""
    from werkzeug.datastructures import FileStorage

    from app.services.settings_service import SettingsService

    with app.app_context():
        SettingsService.update_media(
            "logo_path",
            FileStorage(stream=io.BytesIO(png_bytes), filename="logo.png", content_type="image/png"),
        )
        return True


# ----------------------------------------------------------------------
# 1. 根因：主表單內不得有巢狀 <form>
# ----------------------------------------------------------------------
def test_person_form_has_no_nested_form(logged_in_client, person_with_photo):
    """人物編輯頁（有照片）的主表單內不得出現巢狀 <form>。"""
    html = _get_html(logged_in_client, f"/admin/people/{person_with_photo['id']}/edit")

    assert "<form" not in _main_form_html(html), (
        "admin-form 內出現巢狀 <form>。HTML 不允許 form 巢狀，"
        "內層的 </form> 會提前關閉主表單，導致「儲存」按鈕失去 form owner "
        "而完全失效。請改用 <button form=\"...\"> 把實際的 form 放到主表單之外。"
    )


def test_research_form_has_no_nested_form(logged_in_client, output_with_image):
    """成果編輯頁（有主圖）的主表單內不得出現巢狀 <form>。"""
    html = _get_html(logged_in_client, f"/admin/research/{output_with_image['id']}/edit")

    assert "<form" not in _main_form_html(html), "research_form 的 admin-form 內出現巢狀 <form>"


def test_settings_form_has_no_nested_form(logged_in_client, settings_with_logo):
    """網站設定頁（有 logo）的主表單內不得出現巢狀 <form>。"""
    html = _get_html(logged_in_client, "/admin/settings")

    assert "<form" not in _main_form_html(html), "settings 的 admin-form 內出現巢狀 <form>"


# ----------------------------------------------------------------------
# 2. 症狀：儲存按鈕與所有欄位必須留在主表單內
# ----------------------------------------------------------------------
def test_person_form_save_button_is_inside_main_form(logged_in_client, person_with_photo):
    """「儲存變更」按鈕必須落在主表單範圍內。

    按鈕若落在主表單之外就沒有 form owner，依 HTML 規範
    點擊時「什麼都不會發生」—— 頁面正常、按鈕可見、
    卻永遠存不了資料。
    """
    html = _get_html(logged_in_client, f"/admin/people/{person_with_photo['id']}/edit")

    assert "儲存變更" in _main_form_html(html), (
        "「儲存變更」按鈕落在主表單之外，點擊不會送出任何請求"
    )


#: 涵蓋照片區塊「之前」與「之後」的欄位。
#: 之後的那些（sort_order / is_featured）正是巢狀 form 缺陷發生時
#: 會被踢出表單的欄位，因此必須留在清單裡。
@pytest.mark.parametrize(
    "field_name",
    ["name_zh", "photo_alt_zh", "photo_alt_en", "sort_order", "is_featured"],
)
def test_person_form_keeps_all_fields_inside_main_form(
    logged_in_client, person_with_photo, field_name
):
    """所有欄位都必須在主表單內，否則送出時不會被帶上。

    特別針對排在照片區塊「之後」的欄位：主表單一旦被提前關閉，
    這些欄位在畫面上看得到、填得了，送出時卻完全不會送出去。
    """
    html = _get_html(logged_in_client, f"/admin/people/{person_with_photo['id']}/edit")

    assert f'name="{field_name}"' in _main_form_html(html), (
        f"欄位 {field_name} 落在主表單之外，填了也存不進去"
    )


# ----------------------------------------------------------------------
# 3. 症狀：移除媒體必須是獨立的表單，且指向正確端點
# ----------------------------------------------------------------------
def test_person_photo_delete_has_its_own_form(logged_in_client, person_with_photo):
    """「移除照片」必須送到刪除端點，而不是被主表單吸收。

    巢狀 <form> 被丟棄時，移除按鈕會變成主表單的一部分，
    點下去會送出整份人物表單到編輯端點 —— 照片永遠刪不掉。
    """
    person_id = person_with_photo["id"]
    html = _get_html(logged_in_client, f"/admin/people/{person_id}/edit")

    delete_action = f"/admin/people/{person_id}/photo/delete"
    assert delete_action in html, "頁面應提供移除照片的操作"
    assert delete_action not in _main_form_html(html), (
        "移除照片的表單被包在主表單內，按鈕會改送出主表單而非刪除照片"
    )
