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
#   test_archive_requires_confirmation
#   test_archive_executes_after_confirmation
#   test_photo_delete_requires_confirmation
#   test_confirmation_page_states_impact_and_reversibility
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


def _reload(model, pk):
    """在新的 app context 中重新讀取一筆資料（避免 detached instance）。"""
    from app.extensions import db

    return db.session.get(model, pk)


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


# ----------------------------------------------------------------------
# 破壞性操作的二次確認（SAI §7.6）
# ----------------------------------------------------------------------
def test_archive_requires_confirmation(logged_in_client, sample_person, app):
    """封存不得單擊即生效，必須先顯示確認頁。"""
    from app.models.person import Person

    response = logged_in_client.post(
        f"/admin/people/{sample_person['id']}/archive", follow_redirects=False
    )

    assert response.status_code == 200, "第一次 POST 應回傳確認頁而非直接執行"
    assert "確定要封存" in response.get_data(as_text=True)

    with app.app_context():
        person = _reload(Person, sample_person["id"])
        assert person.publish_status != "archived", "確認前不得真的封存"


def test_archive_executes_after_confirmation(logged_in_client, sample_person, app):
    """帶 confirmed=1 之後才真正執行。"""
    from app.models.person import Person

    response = logged_in_client.post(
        f"/admin/people/{sample_person['id']}/archive",
        data={"confirmed": "1"},
        follow_redirects=False,
    )

    assert response.status_code == 302, "確認後應執行並轉址（PRG）"

    with app.app_context():
        person = _reload(Person, sample_person["id"])
        assert person.publish_status == "archived"


def test_photo_delete_requires_confirmation(logged_in_client, app, png_bytes):
    """移除照片同樣需要確認 —— 檔案刪掉就回不來了。"""
    import io

    from werkzeug.datastructures import FileStorage

    from app.models.person import Person
    from app.services.person_service import PersonService

    with app.app_context():
        person = PersonService.create(
            {"name_zh": "確認測試", "status": "current", "research_focus_zh": "x"}
        )
        PersonService.attach_photo(
            person,
            FileStorage(stream=io.BytesIO(png_bytes), filename="p.png", content_type="image/png"),
            alt_zh="測試照片",
        )
        person_id = person.id

    response = logged_in_client.post(
        f"/admin/people/{person_id}/photo/delete", follow_redirects=False
    )
    assert response.status_code == 200, "第一次 POST 應回傳確認頁"

    with app.app_context():
        assert _reload(Person, person_id).photo_path, "確認前不得刪除照片"


def test_confirmation_page_states_impact_and_reversibility(logged_in_client, sample_person):
    """確認頁必須說明「會影響什麼」與「能不能救回來」。

    只寫「確定嗎？」等於沒有資訊；使用者無法據以判斷。
    """
    html = logged_in_client.post(
        f"/admin/people/{sample_person['id']}/archive", follow_redirects=False
    ).get_data(as_text=True)

    assert "/people/" in html, "應說明前台網址會受影響"
    assert "可" in html and "重新發布" in html, "應說明資料保留且可還原"
