# ============================================================
# NTUST SiPh Lab - Authentication & Security Tests
#
# 上下游：
#   tests/conftest.py（fixture）-> 本檔
#       -> blueprints/auth/routes.py
#       -> models/admin_user.py、models/audit_log.py
#       -> extensions（login_manager / csrf / limiter）
#
# 檔案路徑：
#   tests/test_auth.py
#
# 建立日期：2026-08-14
# 最後重大修改：2026-08-14
# 版本：v1.0
#
# 模組定位（SAI §20 測試策略）：
#   涵蓋 Route（auth）與 Security（CSRF / session / rate limit）
#   兩個測試層級。
#
# 對應驗收條目：
#   AC-01 未登入進 /admin -> /admin/login
#   AC-02 錯密碼不建立 session；連續錯誤觸發 rate limit
#   AC-03 正確帳密登入後 /admin 可見
#   AC-16 鍵盤可完成 admin login（以表單結構驗證）
#   SAI §11.2 CSRF：缺 token 的 POST = 400/403
#
# 驗證方式：
#   pytest tests/test_auth.py -v
# ============================================================

from __future__ import annotations

import pytest

from tests.conftest import TEST_ADMIN_PASSWORD, TEST_ADMIN_USERNAME


# ----------------------------------------------------------------------
# AC-01：未登入存取後台一律導向登入頁
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "path",
    [
        "/admin",
        "/admin/",
        "/admin/people",
        "/admin/people/new",
        "/admin/research",
        "/admin/research/new",
        "/admin/alumni",
        "/admin/settings",
        "/admin/system",
        "/admin/password",
    ],
)
@pytest.mark.acceptance
def test_ac01_unauthenticated_admin_redirects_to_login(client, path):
    """AC-01：未登入進 /admin/* 一律 302 導向 /admin/login。

    為什麼要逐一測試所有後台路徑：
      權限保護是以 blueprint 層級的 before_request 實作。
      若有人日後改成逐個 route 加裝飾器，就可能漏掉某個路徑。
      這個參數化測試會立刻發現。
    """
    response = client.get(path)

    assert response.status_code == 302, f"{path} 應導向登入頁"
    assert "/admin/login" in response.headers["Location"], (
        f"{path} 的轉址目標應為 /admin/login，實際為 {response.headers['Location']}"
    )


@pytest.mark.acceptance
def test_ac01_login_page_is_publicly_accessible(client):
    """登入頁本身必須可匿名存取，否則沒有人能登入。"""
    response = client.get("/admin/login")
    assert response.status_code == 200


# ----------------------------------------------------------------------
# AC-03：正確帳密可登入
# ----------------------------------------------------------------------
@pytest.mark.acceptance
def test_ac03_valid_credentials_grant_access(client, admin_user):
    """AC-03：正確帳密登入後 /admin 可見。"""
    response = client.post(
        "/admin/login",
        data={"username": TEST_ADMIN_USERNAME, "password": TEST_ADMIN_PASSWORD},
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/")  or response.headers["Location"].endswith("/admin")

    dashboard = client.get("/admin")
    assert dashboard.status_code == 200
    assert "Dashboard" in dashboard.get_data(as_text=True)


@pytest.mark.acceptance
def test_ac03_username_is_case_insensitive(client, admin_user):
    """帳號正規化：大小寫不同仍可登入（SAI §8.2 normalized）。"""
    response = client.post(
        "/admin/login",
        data={"username": TEST_ADMIN_USERNAME.upper(), "password": TEST_ADMIN_PASSWORD},
    )
    assert response.status_code == 302


def test_authenticated_user_visiting_login_is_redirected(logged_in_client):
    """SAI §7.1：已登入者造訪 /admin/login -> 302 -> /admin。"""
    response = logged_in_client.get("/admin/login")
    assert response.status_code == 302
    assert "/admin" in response.headers["Location"]


# ----------------------------------------------------------------------
# AC-02：錯誤密碼不建立 session
# ----------------------------------------------------------------------
@pytest.mark.acceptance
def test_ac02_wrong_password_creates_no_session(client, admin_user):
    """AC-02：錯密碼不建立 session。"""
    response = client.post(
        "/admin/login",
        data={"username": TEST_ADMIN_USERNAME, "password": "definitely-wrong-password"},
    )

    # 重新顯示表單（200），而非導向後台。
    assert response.status_code == 200

    # 關鍵：session 未建立，因此後台仍不可存取。
    dashboard = client.get("/admin")
    assert dashboard.status_code == 302
    assert "/admin/login" in dashboard.headers["Location"]


@pytest.mark.acceptance
def test_ac02_unknown_user_and_wrong_password_are_indistinguishable(client, admin_user):
    """帳號不存在與密碼錯誤必須回應完全相同。

    為什麼重要：
      若兩者訊息不同，攻擊者可先列舉出有效帳號再集中猜密碼
      （user enumeration）。這是 auth/routes.py 刻意的設計，
      本測試防止日後有人「為了使用者體驗」而拆開訊息。
    """
    wrong_password = client.post(
        "/admin/login",
        data={"username": TEST_ADMIN_USERNAME, "password": "wrong-password-here"},
    )
    unknown_user = client.post(
        "/admin/login",
        data={"username": "no-such-account", "password": "wrong-password-here"},
    )

    assert wrong_password.status_code == unknown_user.status_code

    def error_messages(response) -> list[str]:
        """抽出頁面上的錯誤訊息。

        為什麼不直接比對整份 HTML：
          表單會把使用者輸入的帳號回填到 input 的 value，
          兩次請求的帳號本來就不同，整份 HTML 必然有差異。
          那是使用者自己送出的內容，不構成資訊洩漏。
          真正必須相同的是「系統告訴使用者失敗原因的那句話」。
        """
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(response.get_data(as_text=True), "html.parser")
        return sorted(
            node.get_text(strip=True) for node in soup.select(".flash, .form-field__error")
        )

    assert error_messages(wrong_password) == error_messages(unknown_user), (
        "帳號不存在與密碼錯誤的錯誤訊息必須完全相同（防止帳號列舉）"
    )
    # 訊息本身不得透露帳號是否存在。
    for message in error_messages(wrong_password):
        assert "不存在" not in message
        assert "查無" not in message


@pytest.mark.acceptance
def test_ac02_failed_login_writes_audit_log(app, client, admin_user):
    """登入失敗必須留下稽核紀錄（SAI §11.2「audit」）。"""
    from sqlalchemy import select

    from app.extensions import db
    from app.models.audit_log import AuditLog
    from app.models.mixins import AuditAction

    client.post(
        "/admin/login",
        data={"username": TEST_ADMIN_USERNAME, "password": "wrong-password-here"},
    )

    with app.app_context():
        entries = db.session.scalars(
            select(AuditLog).where(AuditLog.action == AuditAction.LOGIN_FAILED)
        ).all()
        assert len(entries) == 1
        # 稽核紀錄絕不可包含密碼（SAI §19 No secret logging）。
        assert "wrong-password-here" not in (entries[0].summary or "")


def test_successful_login_writes_audit_log(app, client, admin_user):
    """登入成功也必須留下稽核紀錄。"""
    from sqlalchemy import select

    from app.extensions import db
    from app.models.audit_log import AuditLog
    from app.models.mixins import AuditAction

    client.post(
        "/admin/login",
        data={"username": TEST_ADMIN_USERNAME, "password": TEST_ADMIN_PASSWORD},
    )

    with app.app_context():
        entries = db.session.scalars(
            select(AuditLog).where(AuditLog.action == AuditAction.LOGIN)
        ).all()
        assert len(entries) == 1
        assert TEST_ADMIN_PASSWORD not in (entries[0].summary or "")
        # IP 必須是雜湊值，不是明碼（SAI §8.8 隱私設計）。
        if entries[0].ip_hash:
            assert entries[0].ip_hash != "127.0.0.1"


@pytest.mark.acceptance
def test_ac02_rate_limit_blocks_repeated_failures(limiter_app):
    """AC-02：連續錯誤觸發 rate limit（429）。

    使用 limiter_app fixture 局部開啟頻率限制 —— 一般測試會關閉它，
    但 SAI §20 要求此機制本身必須被測到。
    """
    from app.extensions import db
    from app.models.admin_user import AdminUser

    with limiter_app.app_context():
        user = AdminUser(username=TEST_ADMIN_USERNAME)
        user.set_password(TEST_ADMIN_PASSWORD)
        db.session.add(user)
        db.session.commit()

    # 設定為每分鐘 3 次，讓測試不必送出 5 次以上。
    limiter_app.config["LOGIN_RATE_LIMIT"] = "3 per minute"

    test_client = limiter_app.test_client()
    statuses = []
    for _ in range(6):
        response = test_client.post(
            "/admin/login",
            data={"username": TEST_ADMIN_USERNAME, "password": "wrong-password-x"},
        )
        statuses.append(response.status_code)

    assert 429 in statuses, (
        f"連續登入失敗應觸發 429 rate limit，實際狀態碼序列：{statuses}"
    )


# ----------------------------------------------------------------------
# CSRF（SAI §11.2：缺 token 的 POST = 400/403）
# ----------------------------------------------------------------------
def test_csrf_missing_token_is_rejected(csrf_app):
    """缺 CSRF token 的 POST 必須被拒絕。"""
    from app.extensions import db
    from app.models.admin_user import AdminUser

    with csrf_app.app_context():
        user = AdminUser(username=TEST_ADMIN_USERNAME)
        user.set_password(TEST_ADMIN_PASSWORD)
        db.session.add(user)
        db.session.commit()

    test_client = csrf_app.test_client()
    response = test_client.post(
        "/admin/login",
        data={"username": TEST_ADMIN_USERNAME, "password": TEST_ADMIN_PASSWORD},
    )

    assert response.status_code in (400, 403), (
        f"缺 CSRF token 的 POST 應回 400/403，實際為 {response.status_code}"
    )
    # 錯誤頁不得洩漏堆疊（AC-19 的精神）。
    assert "Traceback" not in response.get_data(as_text=True)


def test_csrf_token_present_in_login_form(csrf_app):
    """登入表單必須包含 CSRF token 欄位。"""
    test_client = csrf_app.test_client()
    html = test_client.get("/admin/login").get_data(as_text=True)
    assert 'name="csrf_token"' in html


# ----------------------------------------------------------------------
# 登出
# ----------------------------------------------------------------------
def test_logout_requires_post(logged_in_client):
    """GET 登出必須不被允許（防止 <img src> 觸發的 CSRF）。"""
    response = logged_in_client.get("/admin/logout")
    assert response.status_code == 405


def test_logout_clears_session(logged_in_client):
    """POST 登出後，後台不再可存取。"""
    response = logged_in_client.post("/admin/logout")
    assert response.status_code == 302

    dashboard = logged_in_client.get("/admin")
    assert dashboard.status_code == 302
    assert "/admin/login" in dashboard.headers["Location"]


# ----------------------------------------------------------------------
# 密碼政策與雜湊
# ----------------------------------------------------------------------
def test_password_is_never_stored_in_plaintext(app):
    """DB 永不儲存明碼（SAI §11.1）。"""
    from app.models.admin_user import AdminUser

    with app.app_context():
        user = AdminUser(username="hashcheck")
        user.set_password("a-very-secret-password")

        assert user.password_hash != "a-very-secret-password"
        assert "a-very-secret-password" not in user.password_hash
        assert user.verify_password("a-very-secret-password") is True
        assert user.verify_password("wrong") is False


def test_short_password_is_rejected(app):
    """密碼長度下限 12 字元（SAI §11.2 強密碼）。"""
    from app.models.admin_user import AdminUser

    with app.app_context():
        user = AdminUser(username="shortpass")
        with pytest.raises(ValueError):
            user.set_password("short")


def test_empty_password_never_verifies(app):
    """空密碼或空 hash 一律驗證失敗。"""
    from app.models.admin_user import AdminUser

    with app.app_context():
        user = AdminUser(username="emptycheck")
        assert user.verify_password("") is False
        assert user.verify_password("anything") is False  # password_hash 尚未設定


def test_inactive_account_cannot_log_in(app, client):
    """停用帳號無法登入（即使密碼正確）。"""
    from app.extensions import db
    from app.models.admin_user import AdminUser

    with app.app_context():
        user = AdminUser(username="disableduser", is_active=False)
        user.set_password(TEST_ADMIN_PASSWORD)
        db.session.add(user)
        db.session.commit()

    response = client.post(
        "/admin/login",
        data={"username": "disableduser", "password": TEST_ADMIN_PASSWORD},
    )
    assert response.status_code == 200  # 重新顯示表單，未登入

    assert client.get("/admin").status_code == 302


# ----------------------------------------------------------------------
# AC-16：鍵盤可完成登入（以表單結構驗證）
# ----------------------------------------------------------------------
@pytest.mark.acceptance
def test_ac16_login_form_is_keyboard_accessible(client):
    """AC-16：登入表單具備鍵盤操作所需的結構。

    以 HTML 結構驗證而非模擬按鍵：
      真正的鍵盤走訪需要瀏覽器，已於開發過程以 preview 工具人工確認。
      這裡驗證的是「結構不會退化」——
      label 關聯、autocomplete 與原生 submit button 缺一，
      鍵盤與密碼管理器的體驗就會損壞。
    """
    html = client.get("/admin/login").get_data(as_text=True)

    # label 與 input 以 id 關聯。
    assert 'for="username"' in html and 'id="username"' in html
    assert 'for="password"' in html and 'id="password"' in html

    # 密碼管理器所需的 autocomplete。
    assert 'autocomplete="username"' in html
    assert 'autocomplete="current-password"' in html

    # 原生 submit button（可用 Enter 或 Space 觸發）。
    assert 'type="submit"' in html


# ----------------------------------------------------------------------
# 密碼變更
# ----------------------------------------------------------------------
def test_change_password_requires_current_password(logged_in_client):
    """修改密碼必須提供正確的目前密碼。"""
    response = logged_in_client.post(
        "/admin/password",
        data={
            "current_password": "wrong-current-password",
            "new_password": "brand-new-password-99",
            "confirm_password": "brand-new-password-99",
        },
    )
    assert response.status_code == 200
    assert "目前密碼不正確" in response.get_data(as_text=True)


def test_change_password_succeeds_and_new_password_works(app, logged_in_client, admin_user):
    """成功變更密碼後，新密碼可登入、舊密碼失效。"""
    new_password = "brand-new-password-99"

    response = logged_in_client.post(
        "/admin/password",
        data={
            "current_password": TEST_ADMIN_PASSWORD,
            "new_password": new_password,
            "confirm_password": new_password,
        },
    )
    assert response.status_code == 302

    fresh_client = app.test_client()

    old = fresh_client.post(
        "/admin/login",
        data={"username": TEST_ADMIN_USERNAME, "password": TEST_ADMIN_PASSWORD},
    )
    assert old.status_code == 200, "舊密碼必須失效"

    new = app.test_client().post(
        "/admin/login",
        data={"username": TEST_ADMIN_USERNAME, "password": new_password},
    )
    assert new.status_code == 302, "新密碼必須可用"


def test_change_password_rejects_mismatched_confirmation(logged_in_client):
    """兩次輸入不一致時拒絕。"""
    response = logged_in_client.post(
        "/admin/password",
        data={
            "current_password": TEST_ADMIN_PASSWORD,
            "new_password": "brand-new-password-99",
            "confirm_password": "different-password-99",
        },
    )
    assert response.status_code == 200
    assert "不一致" in response.get_data(as_text=True)
