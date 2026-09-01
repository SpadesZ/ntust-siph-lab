# ============================================================
# NTUST SiPh Lab - 後台回饋可見性測試
#
# 上下游：
#   admin/base.html（錯誤摘要）+ AdminForm.Meta.locales（中文化）
#       -> 本測試
#
# 檔案路徑：
#   tests/test_admin_feedback.py
#
# 建立日期：2026-09-01
# 版本：v1.0
#
# 模組定位與責任邊界：
#   守住「使用者一定知道剛才發生了什麼」這件事。
#
#   設定頁與人物頁高達 7000px、逾 80 個欄位。POST 失敗後
#   瀏覽器回到頁面頂端，而使用者按下儲存時視線在底部的
#   sticky 動作列 —— 若錯誤欄位在頁面中段，畫面看起來與送出前
#   完全一樣，使用者會以為已經存好。
#
#   責任邊界（不得做的事）：
#     - 不驗證欄位的業務規則（那是 test_admin_forms.py）。
#     - 不驗證資料是否正確落地（那是 test_admin_input_safety.py）。
#
# 主要 Function：
#   test_validation_failure_shows_error_summary
#   test_error_summary_links_to_each_failing_field
#   test_no_error_summary_when_form_is_clean
#   test_builtin_validation_messages_are_chinese
#
# 依賴套件：pytest, beautifulsoup4
#
# 資料庫使用方式：透過 conftest 的暫存 SQLite。
#
# 已知限制與禁止事項：
#   1. 禁止把錯誤摘要改成只在某些頁面出現 ——
#      它是 base.html 的共同保障。
#
# 驗證方式：
#   pytest tests/test_admin_feedback.py
# ============================================================

from __future__ import annotations


def _soup(html: str):
    from bs4 import BeautifulSoup

    return BeautifulSoup(html, "html.parser")


def _post_settings_with_error(client):
    """送出一份必填欄位為空白字元的設定表單（伺服器端會判定失敗）。"""
    return client.post(
        "/admin/settings",
        data={"lab_name_zh": "   ", "lab_name_en": "Test Lab"},
        follow_redirects=False,
    )


def test_validation_failure_shows_error_summary(logged_in_client):
    """驗證失敗時，頁面頂端必須出現錯誤摘要。"""
    response = _post_settings_with_error(logged_in_client)
    assert response.status_code == 200

    soup = _soup(response.get_data(as_text=True))
    summary = soup.find(id="form-error-summary")

    assert summary is not None, (
        "驗證失敗卻沒有頂層錯誤摘要 —— 在 7000px 的表單上，"
        "使用者會以為已經儲存成功"
    )
    assert summary.get("role") == "alert", "錯誤摘要要讓螢幕閱讀器立即讀出"
    assert "尚未儲存" in summary.get_text(), "摘要要明說『沒有存成功』"


def test_error_summary_links_to_each_failing_field(logged_in_client):
    """摘要中的每一項都要能直接跳到出錯的欄位。"""
    response = _post_settings_with_error(logged_in_client)
    soup = _soup(response.get_data(as_text=True))
    summary = soup.find(id="form-error-summary")

    links = summary.find_all("a")
    assert links, "摘要必須提供跳到欄位的錨點連結"

    for link in links:
        target = link.get("href", "")
        assert target.startswith("#"), f"錨點格式錯誤：{target}"
        assert soup.find(id=target[1:]) is not None, (
            f"錨點 {target} 指向不存在的欄位，點了不會有反應"
        )


def test_no_error_summary_when_form_is_clean(logged_in_client):
    """沒有錯誤時不得出現摘要（避免狼來了）。"""
    html = logged_in_client.get("/admin/settings").get_data(as_text=True)
    assert _soup(html).find(id="form-error-summary") is None


def test_builtin_validation_messages_are_chinese(logged_in_client):
    """WTForms 內建訊息必須是中文。

    整個後台是繁體中文，原本只有 4 個欄位帶 message=，
    其餘約 44 個 validator 會落回英文預設值，
    使用者看到的是「This field is required.」夾在一片中文裡。
    """
    response = _post_settings_with_error(logged_in_client)
    text = response.get_data(as_text=True)

    assert "This field is required" not in text, "驗證訊息不該出現英文預設值"

    soup = _soup(text)
    summary_text = soup.find(id="form-error-summary").get_text()
    assert any("一" <= ch <= "鿿" for ch in summary_text), (
        "錯誤摘要內應為中文訊息"
    )
