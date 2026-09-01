# ============================================================
# NTUST SiPh Lab - 後台 JS 為漸進增強的契約測試
#
# 上下游：
#   app/static/js/admin.js -> templates/admin/base.html
#       -> 本測試（驗證 JS 是「加分」而非「必要」）
#
# 檔案路徑：
#   tests/test_admin_progressive_enhancement.py
#
# 建立日期：2026-09-01
# 版本：v1.0
#
# 模組定位與責任邊界：
#   後台原本完全沒有 JS，理由被記為「CSP 禁止 script」。
#   但實際政策是 script-src 'self' —— 禁止的是 inline script，
#   外部 JS 檔完全允許。admin.js 因此得以存在。
#
#   既然引入了 JS，就必須守住兩件事：
#     1. 它是外部檔案且不含 inline script（CSP 仍然成立）
#     2. 它是漸進增強 —— 沒有它，表單仍然完整可用
#
#   責任邊界（不得做的事）：
#     - 不在此測試 JS 的執行結果（需要瀏覽器環境）；
#       連點防護的實際行為以人工驗證為準。
#
# 主要 Function：
#   test_admin_js_is_loaded_as_external_deferred_file
#   test_admin_pages_have_no_inline_script
#   test_forms_work_without_javascript
#   test_admin_js_does_not_perform_validation
#
# 依賴套件：pytest, beautifulsoup4
#
# 已知限制與禁止事項：
#   1. 禁止讓任何後台功能只能透過 JS 完成。
#
# 驗證方式：
#   pytest tests/test_admin_progressive_enhancement.py
# ============================================================

from __future__ import annotations

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ADMIN_JS = PROJECT_ROOT / "app" / "static" / "js" / "admin.js"

#: 會渲染表單的後台頁面。
FORM_PAGES = ["/admin/settings", "/admin/people/new", "/admin/research/new"]


def _soup(html: str):
    from bs4 import BeautifulSoup

    return BeautifulSoup(html, "html.parser")


def test_admin_js_is_loaded_as_external_deferred_file(logged_in_client):
    """admin.js 必須以外部檔案 + defer 載入。"""
    soup = _soup(logged_in_client.get("/admin/").get_data(as_text=True))
    scripts = soup.find_all("script")

    assert scripts, "後台應載入 admin.js"
    for script in scripts:
        src = script.get("src")
        assert src, "後台不得使用 inline script（CSP script-src 'self'）"
        assert script.has_attr("defer"), f"{src} 應以 defer 載入，避免阻塞渲染"


@pytest.mark.parametrize("path", ["/admin/"] + FORM_PAGES)
def test_admin_pages_have_no_inline_script(logged_in_client, path):
    """任何後台頁面都不得出現 inline script。

    CSP 的 script-src 'self' 會直接擋掉，出現即為破功。
    """
    soup = _soup(logged_in_client.get(path).get_data(as_text=True))

    for script in soup.find_all("script"):
        assert script.get("src"), f"{path} 出現 inline script"
        assert not (script.string or "").strip(), f"{path} 的 script 標籤含有內容"


@pytest.mark.parametrize("path", FORM_PAGES)
def test_forms_work_without_javascript(logged_in_client, path):
    """表單在沒有 JS 的情況下必須完整可用。

    測試 client 本來就不執行 JS，因此這裡驗證的是
    「送出所需的一切都在 HTML 裡」：method、action 與 submit 按鈕。

    這裡刻意不斷言 CSRF token —— TestConfig 關閉了
    WTF_CSRF_ENABLED，斷言它等於在測試環境設定而非程式碼。
    CSRF 由 tests/test_errors.py 與安全測試以 csrf_app 覆蓋。
    """
    soup = _soup(logged_in_client.get(path).get_data(as_text=True))
    form = soup.find("form", class_="admin-form")

    assert form is not None, f"{path} 應有主表單"
    assert form.get("method", "").lower() == "post"
    assert form.get("action"), "表單必須有 action，不能靠 JS 決定送到哪裡"
    assert form.find("button", attrs={"type": "submit"}), (
        "必須有真正的 submit 按鈕，不能靠 JS 觸發送出"
    )


def test_admin_js_does_not_perform_validation():
    """admin.js 不得做欄位驗證。

    驗證的唯一真相在伺服器端。前端若另做一套，只會產生
    「前端說可以、後端說不行」的不一致，讓使用者更困惑。
    """
    source = ADMIN_JS.read_text(encoding="utf-8")

    for forbidden in ("checkValidity", "setCustomValidity", "reportValidity"):
        assert forbidden not in source, (
            f"admin.js 不應呼叫 {forbidden} —— 驗證應由伺服器端負責"
        )
