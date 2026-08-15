# ============================================================
# NTUST SiPh Lab - Application Configuration
#
# 上下游：
#   環境變數 (.env.local / Cloud Run env / Secret Manager)
#       -> Config class hierarchy
#       -> create_app() (app/__init__.py)
#       -> extensions.py (db / migrate / login / csrf / limiter)
#       -> storage/*.py (StorageBackend 選擇)
#       -> services/seo_service.py (PUBLIC_BASE_URL canonical 生成)
#
# 檔案路徑：
#   app/config.py
#
# 建立日期：2026-08-14
# 最後重大修改：2026-08-14
# 版本：v1.0
#
# 模組定位與責任邊界：
#   本檔是「環境差異的唯一收斂點」。SAI §25 明定：程式碼、models、
#   routes、templates、Admin UX 在 local 與 production 完全相同，
#   環境差異「只允許」出現在 configuration、database connection、
#   storage adapter 與 deployment tooling。因此任何 if APP_ENV ==
#   'production' 的分支都應該收在這裡或 storage adapter，不得散落
#   在 blueprint / service / template。
#
#   責任邊界（不得做的事）：
#     - 不得在此讀寫資料庫或建立 SQLAlchemy engine（那是 extensions 的事）。
#     - 不得在此 import models（會造成 circular import）。
#     - 不得在此硬編碼任何 secret 值。
#
# 輸入 -> 處理 -> 輸出 Pipeline：
#   輸入：os.environ（APP_ENV、SECRET_KEY、DATABASE_URL、
#         STORAGE_BACKEND、UPLOAD_DIR、PUBLIC_BASE_URL...）
#   處理：型別轉換 (_env_bool/_env_int)、預設值決策、
#         SQLite 相對路徑解析為 instance 絕對路徑、
#         postgres:// -> postgresql+psycopg:// 正規化
#   輸出：Config 子類別（供 app.config.from_object 使用）
#
# 主要 Class / Function：
#   _env_bool / _env_int / _env_str  - 環境變數型別轉換
#   BaseConfig      - 共用預設值與安全預設
#   LocalConfig     - APP_ENV=local，SQLite + local uploads
#   TestConfig      - pytest，記憶體或暫存 SQLite、關閉 CSRF/limiter
#   ProductionConfig- APP_ENV=production，Cloud Run + PostgreSQL + GCS
#   get_config(name)- 依 APP_ENV 取得對應 config class
#
# 依賴套件：
#   標準庫 os / pathlib；不依賴 Flask 以外的第三方套件。
#
# 環境變數（完整清單見 .env.example 與 SAI 附錄 B）：
#   APP_ENV, SECRET_KEY, PUBLIC_BASE_URL, DATABASE_URL, DB_BACKEND,
#   STORAGE_BACKEND, UPLOAD_DIR, GCS_BUCKET, MAX_CONTENT_LENGTH,
#   SESSION_COOKIE_SECURE, PERMANENT_SESSION_LIFETIME_HOURS,
#   LOGIN_RATE_LIMIT, LOG_LEVEL, ENABLE_LLMS_TXT, ROBOTS_POLICY
#
# 資料庫 / Storage 使用方式：
#   本檔只決定「連線字串」與「backend 名稱」，不建立連線。
#   SQLite 僅為 local/test 用途（ADR-002）；production 一律
#   PostgreSQL（ADR-005）+ Cloud Storage（ADR-006）。
#
# Error Handling / Fallback：
#   - production 缺少 SECRET_KEY 或 PUBLIC_BASE_URL 直接 raise
#     RuntimeError，寧可啟動失敗也不要用弱 secret 上線（SAI §11.1）。
#   - local 缺 SECRET_KEY 時給固定 dev 值並在 log 警告，方便開發。
#   - DATABASE_URL 未設定時 fallback 到 instance/siph_lab.db。
#
# 特殊機制：
#   SQLALCHEMY_ENGINE_OPTIONS 依 backend 分流：SQLite 需要
#   check_same_thread=False（gunicorn 多 worker）與 busy timeout；
#   PostgreSQL 需要 pool_pre_ping 以避免 Cloud SQL 閒置斷線。
#   注意：SQLite 的 PRAGMA foreign_keys 由 extensions.py 的
#   connect event 設定，不在此處（SAI §10.2：SQLite-only 技巧必須
#   隔離在 local adapter）。
#
# 已知限制與禁止事項：
#   1. 禁止把 SQLite 當 production 永久資料庫（ADR-004 / SAI §21）。
#   2. 禁止在 production 讓 SESSION_COOKIE_SECURE=False。
#   3. 禁止將 .env 或任何實際 secret commit 進 repo。
#   4. 本檔不得 import app.models / app.services（circular import）。
#
# 維護契約：
#   新增環境變數時，必須同步更新：
#     (a) .env.example
#     (b) docs/local-development.md 與 docs/cloudrun-deployment.md
#     (c) tests/test_db_portability.py 若影響 DB 行為
#   否則部署到 Cloud Run 會出現「本機可跑、雲端啟動失敗」。
#
# 驗證方式：
#   pytest tests/test_config.py
#   pytest tests/test_db_portability.py
# ============================================================

from __future__ import annotations

import os
from pathlib import Path

#: 專案根目錄（app/config.py -> app/ -> 專案根）。
#: 用途僅限於「local 開發時的預設相對路徑解析」，production 不依賴此值。
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _env_str(key: str, default: str | None = None) -> str | None:
    """讀取環境變數字串，空字串視同未設定。

    為什麼：Cloud Run / docker-compose 常把未設定的變數傳成空字串，
    若直接使用會得到 '' 而非 default，導致 canonical URL 變成空值。
    """
    value = os.environ.get(key)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def _env_bool(key: str, default: bool = False) -> bool:
    """把環境變數轉成 bool。接受 1/true/yes/on（不分大小寫）。

    為什麼：env 只有字串，"false" 是 truthy string，直接 bool() 會全為 True，
    曾是 SESSION_COOKIE_SECURE 類設定最常見的安全性錯誤來源。
    """
    raw = _env_str(key)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _env_int(key: str, default: int) -> int:
    """把環境變數轉成 int；格式錯誤時退回 default 而不讓 app 啟動失敗。"""
    raw = _env_str(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _normalize_database_url(url: str) -> str:
    """正規化 DATABASE_URL，確保 SQLAlchemy 2.x 可用的 driver 名稱。

    為什麼這樣設計：
      許多雲端平台（含部分 GCP 範例與 Heroku 傳統格式）提供的連線字串
      以 'postgres://' 開頭，SQLAlchemy 2.x 已不接受該 scheme。若不在
      config 收斂，錯誤會延後到第一次 DB 查詢才爆，且訊息難以理解。

    修改會影響什麼：
      這是 SQLite <-> PostgreSQL 切換的唯一入口（SAI §10.2 可攜契約）。
      變更此函式等同變更所有環境的連線行為，必須同時跑
      tests/test_db_portability.py 的雙 DB 矩陣。
    """
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


class BaseConfig:
    """所有環境共用的預設值與安全基線。

    設計理由：
      安全相關預設一律採「安全的那一端」（HttpOnly=True、SameSite=Lax、
      CSRF 開啟）。個別環境只能「明確放寬」，不會因為忘記設定而不安全。
    """

    # --- 身分與環境 ---
    APP_ENV: str = "local"
    DEBUG: bool = False
    TESTING: bool = False

    # --- 站台識別（SEO canonical 的根，SAI §12.1）---
    PUBLIC_BASE_URL: str = _env_str("PUBLIC_BASE_URL", "http://localhost:8000")

    # --- SQLAlchemy ---
    SQLALCHEMY_TRACK_MODIFICATIONS = False  # 關閉以免無謂記憶體開銷
    SQLALCHEMY_ENGINE_OPTIONS: dict = {}

    # --- Session / Cookie 安全（SAI §11.1）---
    SESSION_COOKIE_HTTPONLY = True          # 禁止 JS 讀 cookie
    SESSION_COOKIE_SAMESITE = "Lax"         # 防 CSRF 的縱深防禦
    SESSION_COOKIE_SECURE = False           # 由子類別依環境覆寫
    SESSION_COOKIE_NAME = "siph_session"
    #: SAI §11.1 建議 8 小時 absolute session lifetime。
    PERMANENT_SESSION_LIFETIME_HOURS = _env_int("PERMANENT_SESSION_LIFETIME_HOURS", 8)

    # --- CSRF（Flask-WTF）---
    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = None  # 與 session lifetime 綁定，不另設短效期

    # --- 上傳限制（SAI §16）---
    #: 8 MB。超過由 Flask 直接回 413，MediaService 另有副檔名/MIME 檢查。
    MAX_CONTENT_LENGTH = _env_int("MAX_CONTENT_LENGTH", 8 * 1024 * 1024)
    ALLOWED_IMAGE_EXTENSIONS = frozenset({"jpg", "jpeg", "png", "webp"})
    ALLOWED_IMAGE_MIMETYPES = frozenset({"image/jpeg", "image/png", "image/webp"})

    # --- Storage adapter（SAI §10.4）---
    STORAGE_BACKEND = _env_str("STORAGE_BACKEND", "local")
    UPLOAD_DIR = _env_str("UPLOAD_DIR", str(PROJECT_ROOT / "uploads"))
    GCS_BUCKET = _env_str("GCS_BUCKET")

    # --- 備份（SAI §10.6）---
    BACKUP_DIR = _env_str("BACKUP_DIR", str(PROJECT_ROOT / "backups"))

    # --- 登入頻率限制（SAI §11.2 Brute force）---
    #: 格式為 Flask-Limiter 字串；SAI 建議 5 次 / 15 分鐘。
    LOGIN_RATE_LIMIT = _env_str("LOGIN_RATE_LIMIT", "5 per 15 minutes")
    RATELIMIT_ENABLED = True
    RATELIMIT_STORAGE_URI = _env_str("RATELIMIT_STORAGE_URI", "memory://")

    # --- SEO / GEO 開關（SAI §13.2、§15.4 Advanced）---
    #: llms.txt 是「實驗性相容層」，預設關閉；SAI §13.2 明確禁止把它
    #: 描述成 Google 排名必要條件。
    ENABLE_LLMS_TXT = _env_bool("ENABLE_LLMS_TXT", False)
    #: public = 允許索引；private = 全站 Disallow（staging 用）。
    ROBOTS_POLICY = _env_str("ROBOTS_POLICY", "public")

    # --- 顯示時區（SAI §8.9：DB 存 UTC，前台以 Asia/Taipei 顯示）---
    DISPLAY_TIMEZONE = "Asia/Taipei"

    # --- Logging ---
    LOG_LEVEL = _env_str("LOG_LEVEL", "INFO")

    @staticmethod
    def init_app(app) -> None:
        """環境專屬的 app 後處理掛勾。子類別可覆寫。"""
        return None


class LocalConfig(BaseConfig):
    """本機開發與內容驗收環境（ADR-002：Local-first SQLite）。

    為什麼允許弱 SECRET_KEY：
      降低新開發者的環境摩擦（SAI §1「不依賴 GCP、不需先付費」）。
      這個放寬只存在於 local，ProductionConfig 會強制檢查。
    """

    APP_ENV = "local"
    DEBUG = _env_bool("FLASK_DEBUG", False)
    SECRET_KEY = _env_str("SECRET_KEY", "dev-only-insecure-secret-key")
    SESSION_COOKIE_SECURE = _env_bool("SESSION_COOKIE_SECURE", False)

    #: 未設定 DATABASE_URL 時，落在 instance/siph_lab.db。
    #: 注意這裡用 PROJECT_ROOT 而非絕對硬編碼路徑，容器與主機皆可用。
    SQLALCHEMY_DATABASE_URI = _normalize_database_url(
        _env_str("DATABASE_URL", f"sqlite:///{(PROJECT_ROOT / 'instance' / 'siph_lab.db').as_posix()}")
    )

    #: SQLite 特有連線參數。check_same_thread=False 是因為 gunicorn
    #: 以多執行緒/多 worker 服務，SQLAlchemy pool 會跨執行緒重用連線。
    #: timeout=15 對應 SAI §10.3 的 busy timeout 要求。
    SQLALCHEMY_ENGINE_OPTIONS = {
        "connect_args": {"check_same_thread": False, "timeout": 15},
        "pool_pre_ping": True,
    }


class TestConfig(BaseConfig):
    """pytest 專用環境。

    為什麼關閉 CSRF 與 rate limit：
      讓一般功能測試不必每次抓 token。但 SAI §20 要求 CSRF 與 rate limit
      本身必須被測到，因此 tests/test_auth.py 會「局部重新開啟」這兩項，
      而不是完全不測。修改此處請確認該測試仍然有效。
    """

    APP_ENV = "test"
    TESTING = True
    DEBUG = False
    SECRET_KEY = "test-secret-key"
    WTF_CSRF_ENABLED = False
    RATELIMIT_ENABLED = False
    PUBLIC_BASE_URL = "http://localhost"

    SQLALCHEMY_DATABASE_URI = _normalize_database_url(
        _env_str("TEST_DATABASE_URL", "sqlite:///:memory:")
    )
    SQLALCHEMY_ENGINE_OPTIONS = {
        "connect_args": {"check_same_thread": False},
    }


class ProductionConfig(BaseConfig):
    """Cloud Run 正式環境（ADR-004/005/006）。

    為什麼在 __init__ 就 raise：
      SAI §11.1 要求 production SECRET_KEY 必須高熵且由環境注入。
      若缺少就啟動失敗，是刻意的 fail-fast：Cloud Run revision 部署會
      直接失敗並保留舊 revision 提供服務（SAI §21.6 rollback），
      比「用預設 secret 成功上線」安全得多。
    """

    APP_ENV = "production"
    DEBUG = False
    SESSION_COOKIE_SECURE = _env_bool("SESSION_COOKIE_SECURE", True)
    SECRET_KEY = _env_str("SECRET_KEY")

    SQLALCHEMY_DATABASE_URI = _normalize_database_url(_env_str("DATABASE_URL", ""))

    #: PostgreSQL 連線池設定。pool_pre_ping 處理 Cloud SQL 閒置斷線；
    #: pool_recycle 小於 Cloud SQL 預設 idle timeout，避免拿到死連線。
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 1800,
        "pool_size": _env_int("DB_POOL_SIZE", 5),
        "max_overflow": _env_int("DB_MAX_OVERFLOW", 2),
    }

    @staticmethod
    def init_app(app) -> None:
        """production 啟動前的硬性檢查（fail-fast）。"""
        missing = []
        if not app.config.get("SECRET_KEY"):
            missing.append("SECRET_KEY")
        if not app.config.get("SQLALCHEMY_DATABASE_URI"):
            missing.append("DATABASE_URL")
        if not app.config.get("PUBLIC_BASE_URL") or "localhost" in app.config["PUBLIC_BASE_URL"]:
            missing.append("PUBLIC_BASE_URL")
        if missing:
            raise RuntimeError(
                "Production 環境缺少必要設定："
                + ", ".join(missing)
                + "。請透過 Secret Manager / Cloud Run env 注入（SAI §21.4）。"
            )

        # SAI §21.4 / ADR-004：正式環境禁止把 SQLite 當永久資料庫。
        if app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite"):
            raise RuntimeError(
                "Production 禁止使用 SQLite 作為永久資料庫（ADR-004 / SAI §21）。"
            )

        # SAI §21.4：正式環境不得把 uploads 寫進 container filesystem。
        if app.config.get("STORAGE_BACKEND") != "gcs":
            raise RuntimeError(
                "Production 必須使用 STORAGE_BACKEND=gcs（ADR-006 / SAI §21.4）；"
                "Cloud Run container filesystem 不具持久性 [S17]。"
            )


#: APP_ENV 名稱 -> Config class。get_config 之外不要直接使用。
_CONFIG_MAP = {
    "local": LocalConfig,
    "development": LocalConfig,
    "test": TestConfig,
    "testing": TestConfig,
    "production": ProductionConfig,
}


def get_config(config_name: str | None = None):
    """依名稱或 APP_ENV 取得 config class。

    Fallback 行為：未知名稱一律退回 LocalConfig，而不是拋錯，
    因為誤打環境名時「以最保守的本機設定啟動」比整個服務起不來好；
    但 production 的識別字必須精確，否則不會觸發 ProductionConfig
    的 fail-fast 檢查 —— 這是刻意的取捨，部署腳本必須明確設
    APP_ENV=production（見 deploy/cloudrun.md）。
    """
    name = (config_name or os.environ.get("APP_ENV") or "local").strip().lower()
    return _CONFIG_MAP.get(name, LocalConfig)
