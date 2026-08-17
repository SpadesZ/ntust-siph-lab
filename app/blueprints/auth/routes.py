# ============================================================
# NTUST SiPh Lab - Authentication Blueprint (/admin/login)
#
# 上下游：
#   瀏覽器 -> GET/POST /admin/login -> LoginForm -> AdminUser.verify_password
#       -> flask_login.login_user -> session -> 302 /admin
#   POST /admin/logout -> logout_user -> AuditLog -> 302 /admin/login
#   未登入存取 /admin/* -> login_manager.login_view -> 本 blueprint
#
# 檔案路徑：
#   app/blueprints/auth/routes.py
#
# 建立日期：2026-08-14
# 最後重大修改：2026-08-14
# 版本：v1.0
#
# 模組定位與責任邊界：
#   SAI §7.1 定義的登入邊界：
#     未登入 /admin/*        -> 302 -> /admin/login   （AC-01）
#     已登入 /admin/login    -> 302 -> /admin
#   並實作 §11 的密碼驗證、rate limit、session 與稽核。
#
#   責任邊界（不得做的事）：
#     - 不得在此實作密碼雜湊（那是 AdminUser.set_password）。
#     - 不得在此做內容 CRUD。
#     - 不得回傳「帳號不存在 / 密碼錯誤」的區分訊息。
#
# 輸入 -> 處理 -> 輸出 Pipeline：
#   POST /admin/login
#     -> CSRF 驗證（Flask-WTF）
#     -> rate limit 檢查（Flask-Limiter，AC-02）
#     -> 表單驗證
#     -> AdminUser.find_by_username + verify_password
#     -> 成功：login_user + touch_login + AuditLog(login) + 302 /admin
#     -> 失敗：AuditLog(login_failed) + 重新顯示表單（不建立 session）
#
# 主要 Class / Function：
#   LoginForm            - 帳號/密碼表單（含 CSRF）
#   login()              - GET/POST /admin/login
#   logout()             - POST /admin/logout
#   _is_safe_next(url)   - open redirect 防護
#
# 依賴套件：
#   flask, flask-login, flask-wtf, wtforms, app.extensions.limiter
#
# 環境變數（透過 app.config）：
#   LOGIN_RATE_LIMIT - 預設 "5 per 15 minutes"（SAI §11.2）
#
# 資料庫使用方式：
#   admin_users（讀 + 更新 last_login_at）、audit_logs（寫入）。
#
# Error Handling / Fallback：
#   - 帳號不存在與密碼錯誤回傳「完全相同」的訊息與狀態碼。
#     為什麼：區分兩者等於提供帳號列舉（user enumeration）管道，
#     讓攻擊者可先確認帳號存在再集中猜密碼。
#   - rate limit 觸發時由全域 429 handler 處理（SAI §11.2 驗收）。
#
# 特殊機制（open redirect 防護）：
#   ?next= 參數只接受「以單一斜線開頭且非 //」的相對路徑。
#   若不檢查，攻擊者可構造 /admin/login?next=https://evil.example
#   讓使用者登入後被導向釣魚站，且該連結看起來來自可信網域。
#
# 特殊機制（session fixation 防護）：
#   flask_login.login_user() 會在登入時重新產生 session。
#   另外 session.permanent = True 讓 PERMANENT_SESSION_LIFETIME
#   （8 小時 absolute，SAI §11.1）生效 —— 未設定時 Flask 使用
#   browser session cookie，關閉瀏覽器才失效，不符合規格。
#
# 已知限制與禁止事項：
#   1. 不提供「記住我」功能。長效 cookie 與 8 小時 absolute
#      session lifetime 的規格互相矛盾。
#   2. 不提供公開註冊或密碼找回（SAI §1.2 非目標、§11.1）。
#   3. 禁止在 log 或 flash 中出現密碼。
#
# 維護契約：
#   1. 修改失敗訊息時必須保持「兩種失敗完全相同」。
#   2. 移除 rate limit 會直接造成 AC-02 失敗。
#
# 驗證方式：
#   pytest tests/test_auth.py
# ============================================================

from __future__ import annotations

import logging
from urllib.parse import urlparse

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user
from flask_wtf import FlaskForm
from wtforms import PasswordField, StringField
from wtforms.validators import DataRequired, Length

from app.extensions import db, limiter
from app.models.admin_user import AdminUser
from app.models.audit_log import AuditLog
from app.models.mixins import AuditAction

logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__, template_folder="../../templates")

#: 帳號不存在與密碼錯誤共用的訊息（見檔頭 Error Handling）。
_GENERIC_LOGIN_ERROR = "帳號或密碼不正確。"


class LoginForm(FlaskForm):
    """管理員登入表單。

    CSRF token 由 FlaskForm 自動提供（SAI §11.1：
    所有 state-changing admin form 必須有 token）。
    """

    username = StringField(
        "帳號",
        validators=[DataRequired(message="請輸入帳號。"), Length(max=80)],
        render_kw={"autocomplete": "username", "autofocus": True},
    )
    password = PasswordField(
        "密碼",
        validators=[DataRequired(message="請輸入密碼。"), Length(max=200)],
        render_kw={"autocomplete": "current-password"},
    )


def _is_safe_next(target: str | None) -> bool:
    r"""判斷 ?next= 是否為安全的站內相對路徑。

    規則：
      - 必須以 "/" 開頭（站內絕對路徑）。
      - 不得以 "//" 開頭（那是 protocol-relative URL，會離站）。
      - 不得包含反斜線（見下方說明）。
      - 不得包含 scheme 或 netloc。

    為什麼要另外擋反斜線（交付前審查 REV-108）：
      依 WHATWG URL 規範，在 http/https 這類 "special scheme" 下，
      URL 解析器會把 "\" 視同 "/"。因此 "/\evil.example" 在瀏覽器
      眼中等於 "//evil.example" —— 一個 protocol-relative URL，
      會直接離站。但 Python 的 urlparse「不」做這個轉換：
        urlparse("/\\evil.example").netloc == ""   -> 看起來安全
      兩者的認知差異就是繞過點。

      目前這個字串實際上不可利用，因為 Werkzeug 會把 Location
      百分比編碼成 "/%5Cevil.example"，而 "%5C" 不會被解析成
      路徑分隔符。但那是「下游函式庫剛好救了我們」，不是這個
      守衛函式做對了 —— 換個 Werkzeug 版本或關掉
      autocorrect_location_header 就會變成真的開放轉址。
      安全檢查不應依賴呼叫端之外的偶然行為。

    見檔頭「特殊機制（open redirect 防護）」。
    """
    if not target:
        return False
    if "\\" in target:
        return False
    if not target.startswith("/") or target.startswith("//"):
        return False

    parsed = urlparse(target)
    return not parsed.scheme and not parsed.netloc


def _rate_limit_value() -> str:
    """從設定取得登入頻率限制字串。

    以函式而非常數傳入 limiter.limit()，
    讓測試可以覆寫 config 而不需要重新 import 模組。
    """
    return current_app.config.get("LOGIN_RATE_LIMIT", "5 per 15 minutes")


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit(_rate_limit_value, methods=["POST"])
def login():
    """管理員登入（SAI §7.1、§11、AC-01~AC-03）。

    rate limit 只套用在 POST：
      GET 是顯示表單，限制它會讓誤觸的管理員連登入頁都打不開，
      而攻擊者真正消耗的是 POST。
    """
    # 已登入者不需要再看登入表單（SAI §7.1）。
    if current_user.is_authenticated:
        return redirect(url_for("admin.dashboard"))

    form = LoginForm()

    if form.validate_on_submit():
        username = form.username.data or ""
        password = form.password.data or ""

        user = AdminUser.find_by_username(username)

        # 兩種失敗走完全相同的路徑與訊息（見檔頭 Error Handling）。
        if user is None or not user.is_active or not user.verify_password(password):
            AuditLog.write(
                action=AuditAction.LOGIN_FAILED,
                entity_type="system",
                summary=f"登入失敗（帳號：{AdminUser.normalize_username(username)}）",
                admin_user_id=user.id if user is not None else None,
                ip_address=request.remote_addr,
            )
            try:
                db.session.commit()
            except Exception:  # noqa: BLE001 - 稽核寫入失敗不應阻止回應
                db.session.rollback()
                logger.exception("寫入登入失敗稽核紀錄時發生錯誤")

            flash(_GENERIC_LOGIN_ERROR, "error")
            # 回 200 重新顯示表單。不用 401：那會觸發瀏覽器的
            # HTTP Basic 認證對話框，對表單登入是錯誤的語意。
            return render_template("admin/login.html", form=form), 200

        # --- 登入成功 ---
        login_user(user)  # 會重新產生 session（session fixation 防護）
        session.permanent = True  # 啟用 8 小時 absolute lifetime

        user.touch_login()
        AuditLog.write(
            action=AuditAction.LOGIN,
            entity_type="system",
            entity_id=user.id,
            summary=f"管理員 {user.username} 登入成功",
            admin_user_id=user.id,
            ip_address=request.remote_addr,
        )
        try:
            db.session.commit()
        except Exception:  # noqa: BLE001
            db.session.rollback()
            logger.exception("寫入登入稽核紀錄時發生錯誤")

        next_url = request.args.get("next")
        if _is_safe_next(next_url):
            return redirect(next_url)
        return redirect(url_for("admin.dashboard"))

    return render_template("admin/login.html", form=form)


@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    """登出（SAI 附錄 A：POST /admin/logout）。

    為什麼只接受 POST：
      GET 登出可被第三方以 <img src="/admin/logout"> 觸發，
      屬於 CSRF 的一種。POST + CSRF token 可完全避免。
    """
    user_id = current_user.id
    username = current_user.username

    AuditLog.write(
        action=AuditAction.LOGOUT,
        entity_type="system",
        entity_id=user_id,
        summary=f"管理員 {username} 登出",
        admin_user_id=user_id,
        ip_address=request.remote_addr,
    )
    try:
        db.session.commit()
    except Exception:  # noqa: BLE001
        db.session.rollback()
        logger.exception("寫入登出稽核紀錄時發生錯誤")

    logout_user()
    session.clear()

    flash("已登出。", "success")
    return redirect(url_for("auth.login"))
