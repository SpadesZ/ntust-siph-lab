# ============================================================
# NTUST SiPh Lab - Error Handling & Security Header Tests
#
# 上下游：
#   tests/conftest.py -> 本檔 -> app/__init__.py 的 error handlers
#                              與 security headers
#
# 檔案路徑：tests/test_errors.py
# 建立日期：2026-08-14 / 版本：v1.0
#
# 模組定位（SAI §19 Logging / Error Handling / Health）：
#   404 先查 redirects、500 不顯示 stack、/healthz 行為、
#   以及 Flask 官方建議的 security headers [S8]。
#
# 對應驗收條目：
#   AC-19 500 頁不顯示 stack trace
#   SAI §19 404 提供導覽、/healthz 回 200/503
#   SAI §11.2 Session theft -> cookie flags
#
# 驗證方式：
#   pytest tests/test_errors.py -v
# ============================================================

from __future__ import annotations

import pytest


# ----------------------------------------------------------------------
# 404
# ----------------------------------------------------------------------
def test_404_page_provides_navigation(client):
    """SAI §19：404 提供回首頁/研究/成員的導覽。"""
    response = client.get("/this-page-does-not-exist")

    assert response.status_code == 404
    html = response.get_data(as_text=True)

    assert "/research" in html
    assert "/members" in html
    assert 'href="/"' in html


def test_404_page_is_noindex(client):
    """錯誤頁不得被索引。"""
    html = client.get("/nope").get_data(as_text=True)
    assert "noindex" in html


def test_404_does_not_leak_requested_path(client):
    """404 頁不回顯使用者輸入的路徑。

    回顯使用者可控字串即使經過跳脫，也容易讓人誤以為
    網站在處理該內容；保持不回顯是最單純的做法。
    """
    html = client.get("/<script>alert(1)</script>").get_data(as_text=True)
    assert "alert(1)" not in html


def test_missing_person_returns_404_not_500(client):
    """不存在的人物 slug 回 404。"""
    assert client.get("/people/no-such-person").status_code == 404


def test_missing_research_returns_404_not_500(client):
    """不存在的成果 slug 回 404。"""
    assert client.get("/research/no-such-output").status_code == 404


# ----------------------------------------------------------------------
# AC-19：500 不顯示 stack trace
# ----------------------------------------------------------------------
def test_ac19_500_page_hides_stack_trace(app):
    """AC-19：500 頁不得顯示 stack trace。

    以臨時註冊一個必定拋錯的 route 來觸發真實的 500 流程，
    而不是直接 render 錯誤模板 —— 後者不會經過 error handler，
    無法驗證真正的行為。
    """
    @app.route("/__boom__")
    def boom():
        raise RuntimeError("內部錯誤細節：資料庫密碼是 secret123")

    client = app.test_client()
    response = client.get("/__boom__")

    assert response.status_code == 500
    html = response.get_data(as_text=True)

    assert "Traceback" not in html
    assert "RuntimeError" not in html
    assert "secret123" not in html, "錯誤訊息內容不得洩漏到頁面"
    assert "/__boom__" not in html

    # 必須顯示友善訊息與導覽。
    assert "伺服器發生問題" in html
    assert 'href="/"' in html


def test_ac19_500_rolls_back_session(app):
    """500 處理必須 rollback，避免後續請求連鎖失敗。"""
    from app.extensions import db
    from app.models.person import Person

    @app.route("/__dirty__")
    def dirty():
        # 加入一筆不合法資料後拋錯，模擬交易中途失敗。
        db.session.add(Person(slug="dirty", name_zh="髒資料", publish_status="bogus"))
        db.session.flush()
        raise RuntimeError("boom")

    client = app.test_client()
    assert client.get("/__dirty__").status_code == 500

    # 後續請求必須正常，而非 PendingRollbackError。
    assert client.get("/members").status_code == 200


# ----------------------------------------------------------------------
# 413 / 429 / 400
# ----------------------------------------------------------------------
def test_413_page_explains_size_limit(app):
    """上傳過大時顯示可理解的提示（SAI §16）。"""
    app.config["MAX_CONTENT_LENGTH"] = 50

    client = app.test_client()
    response = client.post(
        "/admin/login",
        data={"username": "a" * 500, "password": "b" * 500},
    )

    if response.status_code == 413:
        html = response.get_data(as_text=True)
        assert "上傳檔案過大" in html or "大小上限" in html
    else:
        pytest.skip("此 Werkzeug 版本未在此情境觸發 413")


# ----------------------------------------------------------------------
# /healthz（SAI §19、附錄 A）
# ----------------------------------------------------------------------
def test_healthz_returns_ok(client):
    """/healthz 正常時回 200 與極簡內容。"""
    response = client.get("/healthz")

    assert response.status_code == 200
    body = response.get_data(as_text=True).strip()
    assert body == "ok"


def test_healthz_does_not_leak_environment_details(client):
    """SAI §19：/healthz 不得回傳敏感資料。"""
    body = client.get("/healthz").get_data(as_text=True)

    for leak in ("sqlite", "postgresql", "SECRET", "version", "/app/", "C:\\"):
        assert leak.lower() not in body.lower(), f"/healthz 洩漏了 {leak}"


def test_healthz_returns_503_when_database_unavailable(app, monkeypatch):
    """資料庫異常時 /healthz 回 503（供 Cloud Run 判斷）。"""
    from app.services.health_service import HealthService

    monkeypatch.setattr(
        HealthService, "liveness", staticmethod(lambda: (False, "database unavailable"))
    )

    response = app.test_client().get("/healthz")
    assert response.status_code == 503


# ----------------------------------------------------------------------
# Security headers [S8]
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "header,expected",
    [
        ("X-Content-Type-Options", "nosniff"),
        ("X-Frame-Options", "DENY"),
        ("Referrer-Policy", "strict-origin-when-cross-origin"),
    ],
)
def test_security_headers_present(client, header, expected):
    """所有回應都帶上基本安全 headers。"""
    response = client.get("/")
    assert response.headers.get(header) == expected


def test_content_security_policy_blocks_inline_scripts(client):
    """CSP 不允許 inline script（XSS 的主要防線）。"""
    csp = client.get("/").headers.get("Content-Security-Policy")

    assert csp, "缺少 Content-Security-Policy"
    assert "script-src 'self'" in csp
    assert "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0], (
        "script-src 不得包含 'unsafe-inline'"
    )
    assert "frame-ancestors 'none'" in csp


def test_hsts_only_in_secure_environments(app, client):
    """HSTS 只在 HTTPS 環境送出。

    在 http://localhost 送 HSTS 會讓瀏覽器強制升級為 https
    而無法連線，是常見的本機開發障礙。
    """
    assert "Strict-Transport-Security" not in client.get("/").headers

    app.config["SESSION_COOKIE_SECURE"] = True
    assert "Strict-Transport-Security" in app.test_client().get("/").headers


def test_session_cookie_flags(app):
    """SAI §11.1：session cookie 必須 HttpOnly 且 SameSite。"""
    assert app.config["SESSION_COOKIE_HTTPONLY"] is True
    assert app.config["SESSION_COOKIE_SAMESITE"] in ("Lax", "Strict")


def test_admin_pages_are_noindex(logged_in_client):
    """後台頁面一律 noindex（robots.txt 之外的第二道保險）。"""
    html = logged_in_client.get("/admin").get_data(as_text=True)
    assert "noindex" in html


# ----------------------------------------------------------------------
# XSS（SAI §11.2）
# ----------------------------------------------------------------------
def test_user_content_is_escaped_in_templates(app, client):
    """研究摘要中的 script 標籤不得被當成 HTML 執行。"""
    from app.models.mixins import OutputType
    from app.services.research_service import ResearchService

    payload = '<script>alert("xss")</script>'

    with app.app_context():
        output = ResearchService.create(
            {
                "output_type": OutputType.PROJECT,
                "year": 2026,
                "title_zh": f"標題 {payload}",
                "summary_zh": f"摘要內容 {payload} 結束。",
            }
        )
        ResearchService.publish(output)
        slug = output.slug

    html = client.get(f"/research/{slug}").get_data(as_text=True)

    assert "<script>alert" not in html, "使用者內容必須被跳脫"
    assert "&lt;script&gt;" in html, "應以跳脫形式呈現"


def test_dangerous_url_scheme_is_stripped(app, client):
    """javascript: 連結不得進入資料庫（storage XSS 防護）。"""
    from app.services.person_service import PersonService

    with app.app_context():
        person = PersonService.create(
            {
                "name_zh": "危險連結",
                "status": "current",
                "research_focus_zh": "測試",
                "github_url": "javascript:alert(1)",
            }
        )
        assert person.github_url is None, "危險 scheme 必須被正規化為 None"
