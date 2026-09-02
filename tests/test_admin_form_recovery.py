# ============================================================
# NTUST SiPh Lab - 後台表單的「壞情況復原」契約測試
#
# 上下游：
#   app/static/js/admin.js（送出鎖與解鎖路徑）
#   app/blueprints/admin/routes.py::settings / _repeat_context
#   app/blueprints/admin/forms.py::parse_author_orders
#       -> 本測試
#
# 檔案路徑：
#   tests/test_admin_form_recovery.py
#
# 建立日期：2026-09-02
# 版本：v1.0
#
# 模組定位與責任邊界：
#   既有測試涵蓋「正常流程」與「驗證失敗時不遺失輸入」。
#   本檔補的是更後面一段：當事情已經出錯時，管理者能不能自己走出來。
#
#   四個被鎖住的缺陷（皆為實測發現，非推論）：
#     1. 送出鎖沒有任何解鎖路徑 —— 請求失敗後按鈕永遠停在
#        「處理中…」，表單再也送不出去。Cloud Run minScale=0
#        讓冷啟動逾時成為常態而非例外（deploy/service.yaml）。
#     2. 多值欄位數量不一致會拋 ValueError 變成 500，
#        管理者剛打的整份設定全部消失。
#     3. 驗證失敗時把空白列原樣送回模板，模板再附加固定空白列，
#        表單每失敗一次就長一截（實測研究方向 3 -> 6 -> 9）。
#     4. 作者順序打成非數字時該成員被靜默排除在作者列之外，
#        訊息卻只說「不是數字」。
#
#   責任邊界（不得做的事）：
#     - 不在此測試 JS 的執行結果（需要瀏覽器環境）。
#       JS 部分只做原始碼契約檢查，確保解鎖路徑存在且未被移除。
#
# 主要 Function：
#   test_blank_repeat_rows_do_not_accumulate
#   test_mismatched_repeat_lengths_do_not_500
#   test_admin_js_has_unlock_paths
#   test_admin_js_is_plain_text
#   test_author_order_error_names_the_dropped_member
#
# 依賴套件：pytest, beautifulsoup4
#
# 已知限制與禁止事項：
#   1. 禁止移除 admin.js 的 pageshow 或看門狗解鎖 ——
#      沒有它們，一次逾時就會讓管理者完全卡住。
#   2. 禁止讓多值欄位的解析錯誤變回未捕捉的例外。
#
# 驗證方式：
#   pytest tests/test_admin_form_recovery.py
# ============================================================

from __future__ import annotations

from pathlib import Path

import pytest
from werkzeug.datastructures import MultiDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ADMIN_JS = PROJECT_ROOT / "app" / "static" / "js" / "admin.js"


@pytest.fixture()
def professor_id(app):
    """建立一位成員並回傳 id（避免 detached instance）。"""
    from app.services.person_service import PersonService

    with app.app_context():
        person = PersonService.create(
            {
                "name_zh": "楊教授",
                "status": "faculty",
                "research_focus_zh": "測試研究方向",
            }
        )
        return person.id


def _count_inputs(html: str, name: str) -> int:
    from bs4 import BeautifulSoup

    return len(BeautifulSoup(html, "html.parser").find_all("input", attrs={"name": name}))


# ----------------------------------------------------------------------
# 多值區塊
# ----------------------------------------------------------------------
def _failing_settings_payload(rows: int) -> dict:
    """一份必填欄位為空白字元（伺服器端必定判定失敗）的設定表單。

    多值欄位一律送出 rows 筆，模擬瀏覽器把畫面上每一列都送出。
    """
    return {
        # 空白字元：通過瀏覽器的 required，被伺服器端 DataRequired 擋下。
        "lab_name_zh": "   ",
        "lab_name_en": "Test Lab",
        "research_focus-title_zh": ["矽光子"] + [""] * (rows - 1),
        "research_focus-title_en": ["Silicon Photonics"] + [""] * (rows - 1),
        "research_focus-description_zh": [""] * rows,
        "lab_proof-label_zh": [""] * rows,
        "lab_proof-value_zh": [""] * rows,
        "lab_proof-source": [""] * rows,
        "social_links-label": [""] * rows,
        "social_links-url": [""] * rows,
    }


def test_blank_repeat_rows_do_not_accumulate(logged_in_client):
    """連續驗證失敗時，多值區塊的列數必須收斂而非持續增長。

    模板一律在既有資料後附加固定數量的空白列。若 route 把解析
    結果原樣送回（含空白列），那些空白列會被當成「既有資料」
    再加一輪 —— 修復前實測為 3 -> 6 -> 9 -> 12。

    正確行為是「有內容的列數 + 固定空白列數」：使用者填了一列，
    列數從 3 變成 4 是應該的；重點是再失敗幾次都必須停在 4。
    """
    counts = [
        _count_inputs(
            logged_in_client.get("/admin/settings").get_data(as_text=True),
            "research_focus-title_zh",
        )
    ]
    assert counts[0] > 0, "設定頁應該要有研究方向的輸入列"

    # 連續送出三次同樣會失敗的表單，每次都把畫面上的列數原樣送回。
    for _ in range(3):
        html = logged_in_client.post(
            "/admin/settings", data=_failing_settings_payload(counts[-1])
        ).get_data(as_text=True)
        counts.append(_count_inputs(html, "research_focus-title_zh"))

    # 第一次之後就必須穩定下來（內容沒再變，列數就不該再變）。
    assert counts[1] == counts[2] == counts[3], (
        f"列數持續增長：{counts} —— 空白列被當成既有資料送回模板了"
    )
    assert counts[-1] <= counts[0] + 1, (
        f"列數 {counts} 增長超出「使用者實際填寫的一列」，仍有空白列殘留"
    )


def test_failed_submit_still_preserves_typed_repeat_values(logged_in_client):
    """過濾空白列不得順手把使用者剛填的內容也濾掉。

    這是上一條測試的反向保護：若為了不讓列數增長而改成一律讀
    資料庫，就會重現「剛填好的研究方向悄悄還原」的舊缺陷。
    """
    rows = _count_inputs(
        logged_in_client.get("/admin/settings").get_data(as_text=True),
        "research_focus-title_zh",
    )
    html = logged_in_client.post(
        "/admin/settings", data=_failing_settings_payload(rows)
    ).get_data(as_text=True)

    assert "矽光子" in html, "驗證失敗後使用者剛填的研究方向必須還在畫面上"


def test_mismatched_repeat_lengths_do_not_500(logged_in_client):
    """多值欄位數量不一致時要給可行動的訊息，不得回 500。

    parse_repeated 以 ValueError 標記這個結構性問題是對的，
    但 500 會讓管理者剛打的整份設定消失，而錯誤頁不會告訴他
    發生什麼事，也不會告訴他該怎麼辦。
    """
    response = logged_in_client.post(
        "/admin/settings",
        data={
            "lab_name_zh": "Lab",
            "lab_name_en": "Lab",
            "research_focus-title_zh": ["a", "b"],
            "research_focus-title_en": ["a"],  # 刻意少一個
            "research_focus-description_zh": ["a", "b"],
        },
    )

    assert response.status_code != 500, "多值欄位長度不一致不得變成 500"
    assert response.status_code == 200

    html = response.get_data(as_text=True)
    assert "沒有儲存" in html, "必須明確告訴管理者這次的變更沒有生效"


# ----------------------------------------------------------------------
# admin.js 的解鎖契約
# ----------------------------------------------------------------------
def test_admin_js_has_unlock_paths():
    """送出鎖必須有解鎖路徑，否則一次逾時就永久卡死。

    Cloud Run 的 minScale=0 讓管理者幾乎每次登入都遇到冷啟動，
    加上 8 MB 照片處理有機會撞上 timeoutSeconds=60。
    """
    source = ADMIN_JS.read_text(encoding="utf-8")

    assert "pageshow" in source, (
        "缺少 pageshow 解鎖：送出後按上一頁返回時，"
        "按鈕會停在「處理中…」而表單再也送不出去"
    )
    assert "UNLOCK_AFTER_MS" in source, "缺少逾時看門狗，請求失敗後表單會永久鎖住"
    assert "function unlock(" in source, "缺少 unlock()，沒有任何路徑能還原按鈕狀態"


def test_admin_js_covers_buttons_owned_via_form_attribute():
    """以 form="id" 指過來的外部按鈕也要有送出中回饋。

    「移除照片」「移除主圖」位在主表單的版面裡但送出目標是別的
    表單。只查 form.querySelectorAll 會完全找不到它們 ——
    最需要回饋的破壞性操作反而點下去畫面全無反應。
    """
    source = ADMIN_JS.read_text(encoding="utf-8")

    assert 'button[form="' in source, (
        "submitButtonsOf 必須一併蒐集 button[form=...] 的外部按鈕"
    )


def test_admin_js_detects_file_selection_as_unsaved_change():
    """選了照片但沒儲存就離開，必須觸發未儲存警告。

    FormData 取出的 File 物件 join 之後一律是 "[object File]"，
    純靠值比對永遠判定「沒變更」。
    """
    source = ADMIN_JS.read_text(encoding="utf-8")

    assert 'input[type="file"]' in source, "未儲存警告必須另外偵測檔案欄位"


def test_admin_js_is_plain_text():
    """admin.js 不得含有字面 NUL byte。

    NUL 會讓 git 判定整個檔案是 binary，diff 與 code review
    完全看不到內容 —— 一支負責表單行為的檔案變成無法審查，
    風險遠大於少打幾個字元。需要 NUL 分隔符時用 \\u0000 跳脫。
    """
    raw = ADMIN_JS.read_bytes()

    assert b"\x00" not in raw, (
        "admin.js 含字面 NUL byte，git 會視為 binary 而無法 diff；"
        "請改用 \\u0000 跳脫寫法"
    )


# ----------------------------------------------------------------------
# 作者順序
# ----------------------------------------------------------------------
@pytest.mark.parametrize("bad_value", ["第一", "abc", "0", "-1"])
def test_author_order_error_names_the_dropped_member(app, professor_id, bad_value):
    """順序值無法解析時，訊息必須說出「誰」以及「會被排除」。

    sync_people 以傳入的清單為準，因此被跳過的成員會被解除關聯 ——
    原本掛在這篇成果上的作者，會因為順序欄打錯一個字而消失。
    只說「不是數字」會讓管理者以為那一欄沒生效而已。
    """
    from app.blueprints.admin.forms import ResearchForm

    with app.app_context():
        entries, errors = ResearchForm.parse_author_orders(
            MultiDict({f"author_order-{professor_id}": bad_value}),
            [professor_id],
            {professor_id: "楊教授"},
        )

    assert entries == [], "無法解析的順序值不應產生作者關聯"
    assert errors, "必須回報錯誤"

    message = errors[0]
    assert "楊教授" in message, f"錯誤訊息要指名道姓，目前是：{message}"
    assert "列入" in message, (
        f"錯誤訊息必須說明該成員不會被列入作者，目前是：{message}"
    )


def test_author_order_error_falls_back_without_names(app, professor_id):
    """未提供姓名對照時仍要能產生訊息（不得因此拋例外）。"""
    from app.blueprints.admin.forms import ResearchForm

    with app.app_context():
        entries, errors = ResearchForm.parse_author_orders(
            MultiDict({f"author_order-{professor_id}": "abc"}), [professor_id]
        )

    assert entries == []
    assert errors and str(professor_id) in errors[0]
