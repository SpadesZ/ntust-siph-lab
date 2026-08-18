# ============================================================
# NTUST SiPh Lab - Flask Extension Singletons
#
# 上下游：
#   config.py -> create_app() -> extensions.init_app(app)
#       -> db      : 所有 models / repositories 的 Session 來源
#       -> migrate : Alembic (flask db upgrade)
#       -> login   : blueprints/auth 與所有 @login_required
#       -> csrf    : 所有 admin POST form
#       -> limiter : /admin/login 頻率限制
#
# 檔案路徑：
#   app/extensions.py
#
# 建立日期：2026-08-14
# 最後重大修改：2026-08-14
# 版本：v1.0
#
# 模組定位與責任邊界：
#   持有「未綁定 app」的 extension 實例，供 application factory 模式
#   使用。這樣做的原因是 models 需要在 import 時就取得 db.Model，
#   但 app 尚未建立；若把 db 建在 create_app() 內部會造成 models
#   無法 import。
#
#   責任邊界（不得做的事）：
#     - 不得在此 import 任何 model（circular import：model 需要 db）。
#     - 不得在此定義商業邏輯或 route。
#     - 不得在此讀環境變數（那是 config.py 的責任）。
#
# 輸入 -> 處理 -> 輸出 Pipeline：
#   輸入：已套用 config 的 Flask app 物件
#   處理：各 extension init_app、SQLite PRAGMA event 綁定、
#         Flask-Login 設定（login_view / session protection / user_loader）
#   輸出：可用的 db.session、current_user、csrf token、limiter decorator
#
# 主要物件：
#   db       - SQLAlchemy（DeclarativeBase = Base，供 models 繼承）
#   migrate  - Flask-Migrate / Alembic
#   login_manager - Flask-Login
#   csrf     - Flask-WTF CSRFProtect
#   limiter  - Flask-Limiter
#   init_extensions(app) - 統一初始化進入點
#
# 依賴套件：
#   flask-sqlalchemy, flask-migrate, flask-login, flask-wtf,
#   flask-limiter, sqlalchemy
#
# 環境變數：
#   本檔不直接讀 env；所有值來自 app.config（見 config.py）。
#
# 資料庫使用方式：
#   db.session 為 scoped session，由 Flask-SQLAlchemy 依 request
#   context 管理生命週期。Service 層負責 commit；repositories 只查詢。
#
# Error Handling / Fallback：
#   - user_loader 查不到使用者或帳號 is_active=False 時回 None，
#     Flask-Login 會視為未登入並導向 /admin/login。
#   - limiter 在 RATELIMIT_ENABLED=False（測試）時不作用。
#
# 特殊機制（Thread / Transaction）：
#   SQLite 的 foreign key 約束預設關閉，必須每條連線下 PRAGMA。
#   這裡用 SQLAlchemy 的 "connect" event 綁定，且以 dialect 名稱判斷，
#   確保 PostgreSQL 連線不會執行 SQLite-only 語法（SAI §10.2：
#   SQLite-only 技巧必須隔離）。
#
# 已知限制與禁止事項：
#   1. RATELIMIT_STORAGE_URI 預設 memory://，Cloud Run 多 instance 時
#      各 instance 計數獨立。正式環境若需嚴格全域限流，必須改為
#      Redis/Memorystore 並更新 docs/cloudrun-deployment.md。
#      這是已知限制，不是遺漏（SAI §11.2 只要求「超限暫時拒絕」）。
#   2. 禁止在此模組建立第二個 SQLAlchemy 實例。
#
# 維護契約：
#   1. 新增 extension 一律加在此檔並於 init_extensions 註冊，
#      不要在 create_app() 內臨時建立。
#   2. 修改 SQLite PRAGMA 行為時，必須確認 PostgreSQL 路徑不受影響，
#      並跑 pytest tests/test_db_portability.py。
#
# 驗證方式：
#   pytest tests/test_schema.py::test_sqlite_foreign_keys_enforced
#   pytest tests/test_db_portability.py tests/test_auth.py
# ============================================================

from __future__ import annotations

import logging
import sqlite3

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import LoginManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """所有 model 的宣告式基底。

    為什麼使用 SQLAlchemy 2.x DeclarativeBase 而非舊式 db.Model：
      2.x 風格提供 Mapped[] 型別註記，讓 mypy/IDE 能檢查欄位型別，
      也讓 Alembic autogenerate 對 nullable/length 的推斷更準確 ——
      這直接支撐 SAI §10.2「primary key、nullable、length 必須在
      migration 中明確定義」的可攜契約。
    """


#: 全域 SQLAlchemy 實例。models 透過 `from app.extensions import db` 取得。
db = SQLAlchemy(model_class=Base)

#: Alembic 整合。render_as_batch 於 init_extensions 中依 dialect 設定。
migrate = Migrate()

#: Flask-Login。單一管理員（ADR-007），但仍走標準 session 機制。
login_manager = LoginManager()

#: CSRF 保護。SAI §11.1：所有 state-changing admin form 必須有 token。
csrf = CSRFProtect()

#: 登入頻率限制。key 以 remote address 計（SAI §11.2）。
limiter = Limiter(key_func=get_remote_address)


@event.listens_for(Engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, connection_record):  # pragma: no cover - 由 DB 驅動觸發
    """每條新連線套用 SQLite 必要 PRAGMA。

    為什麼這樣設計：
      SQLite 預設「不強制」foreign key 約束。若不開啟，
      research_output_people 的孤兒資料不會被擋下，
      而同樣的程式在 PostgreSQL 會失敗 —— 這正是 SAI §24 列為
      「SQLite -> PostgreSQL 行為差異」的高風險項。開啟後兩者行為一致。

    為什麼用 isinstance 判斷驅動型別而非 dialect 名稱：
      SAI §10.2 禁止讓 SQLite-only 語法外洩到其他 DB，因此必須
      精確判斷。"connect" 事件收到的 connection_record 是
      SQLAlchemy 的 _ConnectionRecord，它「沒有」dialect 屬性
      （此處曾因誤用 connection_record.dialect 而導致所有連線
      建立失敗）。直接檢查 DBAPI 連線物件的型別是 SQLAlchemy
      官方文件建議的作法，且不依賴任何內部屬性。

    修改會影響什麼：
      關閉 foreign_keys 會讓 tests/test_db_portability.py 的
      外鍵測試失敗，也會讓本機通過但 PostgreSQL 上線失敗。請勿關閉。
    """
    if not isinstance(dbapi_connection, sqlite3.Connection):
        return

    cursor = dbapi_connection.cursor()
    try:
        # 強制外鍵約束，對齊 PostgreSQL 行為（SAI §24 風險對策）。
        cursor.execute("PRAGMA foreign_keys=ON")
        # SAI §10.3：busy timeout，降低本機並行寫入的 "database is locked"。
        cursor.execute("PRAGMA busy_timeout=15000")
    finally:
        cursor.close()


def _load_admin_user(user_id: str):
    """Flask-Login user_loader。

    為什麼在函式內 import model：
      避免 module import 時的 circular import
      （models.admin_user 需要 extensions.db）。

    安全設計：
      即使 session 仍持有有效 user_id，只要帳號被停用（is_active=False）
      就回傳 None，等同立即登出。這讓「停用帳號」不需要等 session 過期。
    """
    from app.models.admin_user import AdminUser  # 延遲 import，避免循環相依

    try:
        pk = int(user_id)
    except (TypeError, ValueError):
        return None

    user = db.session.get(AdminUser, pk)
    if user is None or not user.is_active:
        return None
    return user


def init_extensions(app) -> None:
    """在 application factory 中初始化所有 extension。

    順序說明：
      db 必須先於 migrate；login/csrf/limiter 彼此無順序相依。
      render_as_batch 需在 migrate.init_app 時決定，因此放在此處。
    """
    db.init_app(app)

    # SQLite 不支援多數 ALTER TABLE 操作，Alembic 需以 batch mode
    # 重建資料表來完成欄位變更。PostgreSQL 原生支援 ALTER，
    # 開啟 batch 只會產生無謂的 table 重建，故依 dialect 分流。
    # 這是 SAI §10.2「migration 必須在兩種 DB 都能升到 head」的關鍵。
    is_sqlite = app.config.get("SQLALCHEMY_DATABASE_URI", "").startswith("sqlite")
    migrate.init_app(app, db, render_as_batch=is_sqlite)

    login_manager.init_app(app)
    login_manager.login_view = "auth.login"          # 未登入導向 /admin/login（AC-01）
    login_manager.login_message = "請先登入以存取管理後台。"
    login_manager.login_message_category = "warning"
    # "strong" 會在 user agent / IP 變動時失效 session，降低 session 竊取風險
    # （SAI §11.2 Session theft）。
    login_manager.session_protection = "strong"
    login_manager.user_loader(_load_admin_user)

    csrf.init_app(app)

    # Flask-Limiter 讀 app.config 的 RATELIMIT_* 值。
    limiter.init_app(app)

    logger.debug(
        "Extensions initialised (sqlite=%s, storage=%s)",
        is_sqlite,
        app.config.get("STORAGE_BACKEND"),
    )
