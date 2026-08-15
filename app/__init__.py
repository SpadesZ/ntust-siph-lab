# ============================================================
# NTUST SiPh Lab - Application Factory
#
# 上下游：
#   wsgi.py / gunicorn -> create_app() -> Flask app
#   create_app -> config.get_config()      （環境設定）
#             -> extensions.init_extensions（db/migrate/login/csrf/limiter）
#             -> storage.init_storage       （local | gcs）
#             -> blueprints 註冊            （public / auth / admin）
#             -> context processor / Jinja filter
#             -> error handlers（404 先查 redirects -> 301）
#             -> security headers
#             -> CLI commands（flask admin create / reset-password）
#
# 檔案路徑：
#   app/__init__.py
#
# 建立日期：2026-08-14
# 最後重大修改：2026-08-14
# 版本：v1.0
#
# 模組定位與責任邊界：
#   整個應用的組裝點。採 application factory 模式，讓測試可以
#   建立多個互相隔離的 app 實例（tests/conftest.py 依賴此特性）。
#
#   責任邊界（不得做的事）：
#     - 不得在此定義 route（route 屬於 blueprint）。
#     - 不得在此定義商業邏輯。
#     - 不得在 module 層級建立 app 物件（會讓設定無法切換）。
#
# 輸入 -> 處理 -> 輸出 Pipeline：
#   config_name（或 APP_ENV）
#     -> Config class -> app.config
#     -> extensions / storage 初始化
#     -> blueprint 註冊
#     -> template 全域函式與 filter 注入
#     -> error handler / security header 掛載
#     -> 回傳可服務的 Flask app
#
# 主要 Function：
#   create_app(config_name)     - 主要工廠函式
#   _register_blueprints(app)
#   _register_template_helpers(app)
#   _register_error_handlers(app)
#   _register_security_headers(app)
#   _register_cli(app)
#   _configure_logging(app)
#
# 依賴套件：
#   flask, flask-login, flask-wtf, flask-limiter, flask-sqlalchemy,
#   flask-migrate
#
# 環境變數：
#   全部透過 app/config.py 讀取（見該檔說明）。
#
# 資料庫使用方式：
#   本檔不執行查詢，只初始化 db。schema 由 Alembic 管理
#   （flask db upgrade），刻意不呼叫 db.create_all() ——
#   那會產生沒有 migration 紀錄的資料表，破壞 SAI §10.2 的
#   可攜契約（測試環境例外，見 tests/conftest.py 的說明）。
#
# Error Handling / Fallback：
#   - 404：先查 redirects 表，命中則 301（SAI §9.2、AC-10）；
#     否則顯示含導覽的 404 頁（SAI §19）。
#   - 500：顯示友善錯誤頁，stack trace 只進 server log（AC-19）。
#   - 413：上傳超過 MAX_CONTENT_LENGTH 時的友善提示。
#   - CSRFError：回 400 並說明原因（SAI §11.2 驗收要求）。
#
# 特殊機制（安全 headers）：
#   after_request 統一加上 CSP、X-Content-Type-Options、
#   Referrer-Policy 等。Flask 官方建議檢視 security headers [S8]。
#   CSP 刻意不允許 inline script，但允許 inline style ——
#   因為部分元件需要以 style 屬性傳遞動態尺寸；
#   JSON-LD 使用 <script type="application/ld+json">，
#   該類型不是可執行 script，不受 script-src 限制。
#
# 已知限制與禁止事項：
#   1. 禁止在 production 使用 flask run（AC-20 要求 Gunicorn）。
#   2. 禁止呼叫 db.create_all() 於正式路徑。
#   3. 禁止在 module 層級實例化 app。
#
# 維護契約：
#   1. 新增 blueprint 必須在 _register_blueprints 註冊。
#   2. 新增 template 全域函式必須在 _register_template_helpers 註冊，
#      不可在個別 route 以 render_template(**{...}) 重複傳遞。
#   3. 修改 CSP 前必須確認 JSON-LD 與 admin 表單仍正常運作。
#
# 驗證方式：
#   pytest tests/
#   python -c "from app import create_app; create_app('test')"
# ============================================================

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

from flask import Flask, redirect, render_template, request
from flask_wtf.csrf import CSRFError

from app.config import get_config
from app.extensions import db, init_extensions

#: 應用版本。與 docs/SAI.md 的實作版本對應。
__version__ = "1.0.0"


def create_app(config_name: str | None = None, config_overrides: dict | None = None) -> Flask:
    """建立並設定 Flask application。

    Args:
        config_name: local / test / production。
                     None 時由 APP_ENV 環境變數決定。
        config_overrides: 在 extension 初始化「之前」套用的設定覆寫。

    Returns:
        已完成組裝、可直接服務的 Flask app。

    為什麼需要 config_overrides：
      部分 extension（尤其 Flask-Limiter）會在 init_app() 當下
      把設定值讀進內部狀態，之後再修改 app.config 不會生效。
      測試需要為不同案例建立「CSRF 開啟」或「rate limit 開啟」
      的 app，因此必須有一個在初始化前注入設定的正式管道。
      （此處曾因測試只在 create_app 之後修改 config，
      導致 AC-02 的 rate limit 永遠不會觸發而誤判通過。）

      正式環境不使用此參數 —— 設定一律來自環境變數。
    """
    app = Flask(
        __name__,
        instance_relative_config=False,
        static_folder="static",
        template_folder="templates",
    )

    config_class = get_config(config_name)
    app.config.from_object(config_class)

    # 必須在 _configure_logging 與 init_extensions 之前套用，
    # 才能影響 extension 的初始化行為。
    if config_overrides:
        app.config.update(config_overrides)

    _configure_logging(app)

    # session 絕對有效期（SAI §11.1 建議 8 小時）。
    from datetime import timedelta

    app.permanent_session_lifetime = timedelta(
        hours=app.config.get("PERMANENT_SESSION_LIFETIME_HOURS", 8)
    )

    # 環境專屬的 fail-fast 檢查（ProductionConfig 會在此拋錯）。
    config_class.init_app(app)

    init_extensions(app)

    # storage 必須在 blueprint 之前初始化：
    # template 的 media_url 全域函式會用到它。
    from app.storage import init_storage

    init_storage(app)

    # 匯入所有 model，確保 Alembic metadata 完整（見 models/__init__.py）。
    from app import models  # noqa: F401

    _register_blueprints(app)
    _register_template_helpers(app)
    _register_error_handlers(app)
    _register_security_headers(app)
    _register_cli(app)

    app.logger.info(
        "NTUST SiPh Lab v%s 已啟動（env=%s, storage=%s, db=%s）",
        __version__,
        app.config.get("APP_ENV"),
        app.config.get("STORAGE_BACKEND"),
        "sqlite" if str(app.config.get("SQLALCHEMY_DATABASE_URI", "")).startswith("sqlite") else "postgresql",
    )

    return app


# ----------------------------------------------------------------------
# Logging
# ----------------------------------------------------------------------
def _configure_logging(app: Flask) -> None:
    """設定輸出到 stdout 的一致格式日誌（SAI §19）。

    為什麼輸出到 stdout 而非檔案：
      Docker 與 Cloud Run 都以 stdout/stderr 收集日誌。
      寫檔案在 Cloud Run 上等同寫入會消失的暫存檔案系統 [S17]，
      且無法被 Cloud Logging 擷取。
    """
    level_name = (app.config.get("LOG_LEVEL") or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s [%(name)s] %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
    )

    root = logging.getLogger()
    # 避免 gunicorn reload 或多次 create_app 造成 handler 重複疊加，
    # 導致同一行 log 印很多次。
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        root.addHandler(handler)
    root.setLevel(level)

    app.logger.setLevel(level)


# ----------------------------------------------------------------------
# Blueprints
# ----------------------------------------------------------------------
def _register_blueprints(app: Flask) -> None:
    """註冊三個 blueprint（SAI §9.4）。

    url_prefix 說明：
      public 沒有前綴（/、/about、/members...）。
      auth 與 admin 都掛在 /admin 之下，符合 SAI §7.1 的
      「/admin/login 登入、/admin dashboard」路由表。
    """
    from app.blueprints.admin.routes import admin_bp
    from app.blueprints.auth.routes import auth_bp
    from app.blueprints.public.routes import public_bp

    app.register_blueprint(public_bp)
    app.register_blueprint(auth_bp, url_prefix="/admin")
    app.register_blueprint(admin_bp, url_prefix="/admin")


# ----------------------------------------------------------------------
# Template helpers
# ----------------------------------------------------------------------
def _register_template_helpers(app: Flask) -> None:
    """注入所有 template 共用的全域函式、filter 與 context。"""
    from app.models.site_setting import SiteSetting
    from app.services.media_service import MediaService
    from app.utils.dates import format_date, format_datetime, iso_date, iso_datetime

    @app.context_processor
    def inject_globals():
        """所有 template 都能取得的變數。

        settings 一定存在，因此 template 不需要寫 `{% if settings %}`。

        為什麼要 try/except：
          這個 context processor 對「每一個」template 生效，
          包含 errors/500.html。若資料庫故障時它自己拋例外，
          錯誤頁也會 render 失敗，Flask 會退回顯示預設錯誤頁 ——
          在 debug 模式下那會暴露 stack trace，直接違反 AC-19。
          因此 DB 不可用時退回一個未 persist 的暫時物件，
          讓錯誤頁仍能完整呈現。
        """
        try:
            settings = SiteSetting.get()
        except Exception:  # noqa: BLE001 - 見上方說明
            db.session.rollback()
            app.logger.exception("讀取網站設定失敗，改用暫時預設值")
            settings = SiteSetting(id=1)
            db.session.expunge(settings)

        return {
            "settings": settings,
            "current_year": datetime.now(timezone.utc).year,
            "app_version": __version__,
        }

    @app.template_global("media_url")
    def media_url(object_key: str | None) -> str | None:
        """storage object key -> 公開 URL。

        這是 template 取得媒體網址的唯一途徑，
        確保 template 不知道也不關心後端是 local 還是 GCS
        （SAI §23.1：template 不可假設 /uploads 一定是本機檔案）。
        """
        return MediaService.public_url(object_key)

    @app.template_global("json_ld")
    def json_ld(data):
        """把 dict 序列化為可直接嵌入 <script> 的 JSON-LD。

        ensure_ascii=False 保留中文原字元，讓 structured data
        與頁面可見內容逐字相同（便於 AC-17 的人工比對）。

        為什麼必須回傳 Markup 而非 str：
          Jinja 的 autoescape 會把 JSON 的雙引號轉成 &#34;，
          產生的 <script type="application/ld+json"> 內容
          就不再是合法 JSON，所有結構化資料會失效
          （曾實際發生，導致 §12.2 與 AC-17 無法通過）。
          因此必須明確標記為安全字串。

        安全性（為什麼標記 Markup 仍然安全）：
          1. json.dumps 會跳脫字串內的雙引號與反斜線，
             資料無法「跳出」JSON 字串脈絡。
          2. 唯一的殘餘風險是內容含 "</script>" 提前關閉標籤，
             因此把所有 "<" 轉為 \\u003c —— 這在 JSON 中
             與 "<" 等價，但在 HTML 解析器眼中不是標籤起始。
          3. 資料來源全部是 SchemaService 產生的受控 dict，
             不接受任意使用者 HTML。
        """
        from markupsafe import Markup

        if not data:
            return Markup("{}")
        text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        return Markup(text.replace("<", "\\u003c"))

    # --- Jinja filters ---
    app.jinja_env.filters["local_date"] = format_date
    app.jinja_env.filters["local_datetime"] = format_datetime
    app.jinja_env.filters["iso_date"] = iso_date
    app.jinja_env.filters["iso_datetime"] = iso_datetime

    # trim_blocks/lstrip_blocks 讓輸出 HTML 更乾淨，
    # 減少 server-rendered 頁面的無謂空白（SAI §18）。
    app.jinja_env.trim_blocks = True
    app.jinja_env.lstrip_blocks = True


# ----------------------------------------------------------------------
# Error handlers
# ----------------------------------------------------------------------
def _register_error_handlers(app: Flask) -> None:
    """錯誤處理（SAI §9.2、§19、AC-10、AC-19）。"""
    from app.repositories.settings import resolve_redirect

    @app.errorhandler(404)
    def handle_404(error):  # noqa: ARG001
        """404 前先查 redirects（SAI §9.2 request flow）。

        流程：
          slug not found -> RedirectService checks redirects.old_path
                         -> 301 new_path OR 404

        為什麼在 404 handler 而不是在每個 route：
          任何路徑都可能因為 slug 變更而失效，包含未來新增的頁面。
          放在全域 handler 可確保不遺漏（SAI §12.1
          「舊 URL 不造成無意義 404」）。
        """
        record = resolve_redirect(request.path)
        if record is not None:
            return redirect(record.new_path, code=record.status_code)

        return render_template("errors/404.html"), 404

    @app.errorhandler(403)
    def handle_403(error):  # noqa: ARG001
        return render_template("errors/403.html"), 403

    @app.errorhandler(413)
    def handle_413(error):  # noqa: ARG001
        """上傳超過 MAX_CONTENT_LENGTH（SAI §16）。"""
        limit_mb = app.config.get("MAX_CONTENT_LENGTH", 8 * 1024 * 1024) // 1024 // 1024
        return (
            render_template("errors/413.html", limit_mb=limit_mb),
            413,
        )

    @app.errorhandler(429)
    def handle_429(error):  # noqa: ARG001
        """登入頻率限制觸發（SAI §11.2、AC-02）。"""
        return render_template("errors/429.html"), 429

    @app.errorhandler(CSRFError)
    def handle_csrf(error):
        """缺 CSRF token 的 POST -> 400（SAI §11.2 驗收）。"""
        app.logger.warning("CSRF 驗證失敗：%s %s", request.method, request.path)
        return render_template("errors/400.html", reason=error.description), 400

    @app.errorhandler(500)
    @app.errorhandler(Exception)
    def handle_500(error):
        """未預期錯誤（AC-19：不顯示 stack trace）。

        重要：先 rollback session。若請求在交易中途失敗而不 rollback，
        同一個 session 的後續操作都會拋 PendingRollbackError，
        造成連鎖失敗。
        """
        # 讓 Werkzeug 的 HTTP 例外（404/403 等）維持原本處理。
        from werkzeug.exceptions import HTTPException

        if isinstance(error, HTTPException):
            return error

        try:
            db.session.rollback()
        except Exception:  # noqa: BLE001 - rollback 失敗不應遮蔽原始錯誤
            pass

        # 完整 stack trace 只進 server log（SAI §19）。
        app.logger.exception("未處理的例外：%s %s", request.method, request.path)

        return render_template("errors/500.html"), 500


# ----------------------------------------------------------------------
# Security headers
# ----------------------------------------------------------------------
def _register_security_headers(app: Flask) -> None:
    """為所有回應加上安全 headers [S8]。"""

    @app.after_request
    def set_security_headers(response):
        # 防止瀏覽器猜測 MIME 型別（上傳圖片被當成 HTML 執行的防線）。
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        # 禁止被嵌入 iframe（點擊劫持防護）。
        response.headers.setdefault("X-Frame-Options", "DENY")
        # 跨站導覽時只送出來源網域，不洩漏完整路徑。
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        # 本站不使用這些裝置能力，明確關閉。
        response.headers.setdefault(
            "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
        )

        # CSP：不允許 inline script（見檔頭「特殊機制」）。
        # 'unsafe-inline' 只給 style，因為部分元件需要動態尺寸；
        # script 沒有 'unsafe-inline'，因此任何注入的 <script> 都不會執行。
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            "img-src 'self' data: https:; "
            "style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; "
            "font-src 'self' data:; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'",
        )

        # HSTS 只在 production（HTTPS）啟用；
        # 在 http://localhost 送 HSTS 會讓瀏覽器強制升級 https 而無法連線。
        if app.config.get("SESSION_COOKIE_SECURE"):
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )

        return response


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------
def _register_cli(app: Flask) -> None:
    """註冊 flask CLI 指令（SAI §8.2、§11.1）。"""
    from app.cli import register_cli_commands

    register_cli_commands(app)
