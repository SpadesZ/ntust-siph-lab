# ============================================================
# NTUST SiPh Lab - 後台健壯性收尾
#
# 上下游：
#   forms.AdminForm.Meta.bind_field（strip filter）
#   forms.SiteSettingForm.parse_repeated（索引對齊契約）
#   _macros.confirm_button / confirm_form_target（csrf id）
#   Person/ResearchService（sort_order 清空）
#       -> 本測試
#
# 檔案路徑：
#   tests/test_admin_robustness.py
#
# 建立日期：2026-09-02
# 版本：v1.0
#
# 模組定位與責任邊界：
#   收攏幾個「現在不會出錯、但很容易在日後改動時悄悄壞掉」
#   的性質。共同特徵是：出錯時沒有明顯症狀，
#   資料會安靜地變成錯的。
#
# 主要 Function：
#   test_parse_repeated_rejects_misaligned_columns
#   test_parse_repeated_accepts_aligned_columns
#   test_csrf_token_ids_are_unique_on_a_page
#   test_sort_order_can_be_reset_to_default
#   test_whitespace_only_input_is_stripped_before_validation
#   test_password_field_is_not_stripped
#
# 依賴套件：pytest, beautifulsoup4
#
# 已知限制與禁止事項：
#   1. 禁止在 repeat row 內加入 checkbox（會造成欄位橫向錯位），
#      本檔的第一個測試就是為了讓這件事在開發期爆出來。
#
# 驗證方式：
#   pytest tests/test_admin_robustness.py
# ============================================================

from __future__ import annotations

import pytest
from werkzeug.datastructures import MultiDict


# ----------------------------------------------------------------------
# B22：多值欄位的索引對齊契約
# ----------------------------------------------------------------------
def test_parse_repeated_rejects_misaligned_columns():
    """欄位數量不一致時要立刻拋錯，而不是產生錯位資料。

    模擬「有人在 repeat row 加了 checkbox」：未勾選的 checkbox
    不會出現在 POST 裡，該欄位的 list 就比其他欄位短，
    從那一列開始所有欄位橫向錯位 —— A 的網址配到 B 的名稱上，
    而且完全不會報錯。
    """
    from app.blueprints.admin.forms import SiteSettingForm

    form_data = MultiDict()
    for label in ("研究室 GitHub", "研究室 Facebook"):
        form_data.add("social_links-label", label)
    # 只有第二列勾了 checkbox -> url 少一個
    form_data.add("social_links-url", "https://facebook.com/lab")

    with pytest.raises(ValueError) as exc:
        SiteSettingForm.parse_repeated(form_data, ["label", "url"], "social_links")

    assert "長度不一致" in str(exc.value)
    assert "checkbox" in str(exc.value), "錯誤訊息要指出常見肇因，方便下一個人排查"


def test_parse_repeated_accepts_aligned_columns():
    """正常情況（每欄數量相同）仍要照常運作。"""
    from app.blueprints.admin.forms import SiteSettingForm

    form_data = MultiDict()
    for label, url in [("A", "https://a.example"), ("B", "https://b.example")]:
        form_data.add("social_links-label", label)
        form_data.add("social_links-url", url)

    rows = SiteSettingForm.parse_repeated(form_data, ["label", "url"], "social_links")

    assert rows == [
        {"label": "A", "url": "https://a.example"},
        {"label": "B", "url": "https://b.example"},
    ]


# ----------------------------------------------------------------------
# B23：一頁不得出現重複的 id
# ----------------------------------------------------------------------
@pytest.mark.parametrize("path_key", ["person", "research"])
def test_csrf_token_ids_are_unique_on_a_page(
    logged_in_client, app, png_bytes, path_key, sample_output
):
    """同一頁多個表單時，csrf_token 的 id 不得重複。

    WTForms 對每個表單的 token 都產生 id="csrf_token"，
    實測一頁曾出現 4 個相同 id —— 那是無效的 HTML，
    也讓 getElementById 與螢幕閱讀器定位變得不可預期。
    """
    import io

    from bs4 import BeautifulSoup
    from werkzeug.datastructures import FileStorage

    from app.services.person_service import PersonService

    if path_key == "person":
        with app.app_context():
            person = PersonService.create(
                {"name_zh": "重複 id 測試", "status": "current", "research_focus_zh": "x"}
            )
            PersonService.attach_photo(
                person,
                FileStorage(
                    stream=io.BytesIO(png_bytes), filename="p.png", content_type="image/png"
                ),
                alt_zh="測試照片",
            )
            path = f"/admin/people/{person.id}/edit"
    else:
        path = f"/admin/research/{sample_output['id']}/edit"

    response = logged_in_client.get(path)
    # 這行不可省：少了它，頁面 500 時解析到的是錯誤頁，
    # 錯誤頁沒有重複 id，測試就會「因為壞掉而通過」。
    assert response.status_code == 200, f"{path} 應正常渲染，實際 {response.status_code}"

    soup = BeautifulSoup(response.get_data(as_text=True), "html.parser")

    ids = [el["id"] for el in soup.find_all(attrs={"id": True})]
    duplicates = {i for i in ids if ids.count(i) > 1}

    assert not duplicates, f"{path} 出現重複的 id：{sorted(duplicates)}"


# ----------------------------------------------------------------------
# B24：排序值要能清空還原預設
# ----------------------------------------------------------------------
def test_sort_order_can_be_reset_to_default(app):
    """清空排序值應回到預設 100，而不是保留舊數字。

    原本的 `if data.get("sort_order") is not None` 讓「清空」
    變成不可能：管理者清空欄位後儲存，看到的仍是舊數字。
    """
    from app.extensions import db
    from app.models.person import Person
    from app.services.person_service import PersonService

    with app.app_context():
        person = PersonService.create(
            {"name_zh": "排序測試", "status": "current", "sort_order": 5}
        )
        person_id = person.id
        assert person.sort_order == 5

        PersonService.update(person, {"name_zh": "排序測試", "status": "current",
                                      "sort_order": ""})

        assert db.session.get(Person, person_id).sort_order == 100, (
            "清空排序值應回到預設 100"
        )


# ----------------------------------------------------------------------
# B25：空白字元不得繞過必填
# ----------------------------------------------------------------------
def test_whitespace_only_input_is_stripped_before_validation(app):
    """純空白的必填欄位要被判為未填，且前後空白不進資料庫。"""
    from app.blueprints.admin.forms import SiteSettingForm

    with app.test_request_context(
        method="POST", data={"lab_name_zh": "   ", "lab_name_en": "  Test Lab  "}
    ):
        form = SiteSettingForm()
        assert not form.validate(), "純空白不該通過必填驗證"
        assert form.lab_name_en.data == "Test Lab", "前後空白應在驗證前就去掉"


def test_password_field_is_not_stripped(app):
    """密碼欄位不得被 strip —— 空白是使用者輸入的一部分。"""
    from app.blueprints.admin.forms import ChangePasswordForm

    secret = "  spaced password 12  "
    with app.test_request_context(
        method="POST",
        data={
            "current_password": secret,
            "new_password": secret,
            "confirm_password": secret,
        },
    ):
        form = ChangePasswordForm()
        form.validate()
        assert form.current_password.data == secret, (
            "密碼被 strip 會讓含空白的既有密碼登入失敗"
        )
