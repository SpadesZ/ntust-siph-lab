# ============================================================
# NTUST SiPh Lab - Configuration Tests
#
# 檔案路徑：tests/test_config.py
# 建立日期：2026-08-15 / 版本：v1.0
#
# 模組定位（SAI §25、§11.1、§21.4、ADR-004/005/006）：
#   config.py 是「環境差異的唯一收斂點」。ProductionConfig 的
#   fail-fast 檢查是防止不安全設定上線的最後一道防線，
#   而這道防線本身在正式部署前不會被執行到 ——
#   因此必須用測試證明它真的會擋。
#
# 重點：
#   - production 缺必要設定必須啟動失敗（而非用預設值上線）
#   - production 禁止 SQLite（ADR-004）與 local storage（ADR-006）
#   - 安全預設值採「安全的那一端」
#   - _env_bool 正確處理 "false" 字串
#
# 驗證方式：
#   pytest tests/test_config.py -v
# ============================================================

from __future__ import annotations

import pytest

from app.config import (
    BaseConfig,
    LocalConfig,
    ProductionConfig,
    TestConfig,
    _env_bool,
    _normalize_database_url,
    get_config,
)


# ----------------------------------------------------------------------
# 環境變數型別轉換
# ----------------------------------------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ("1", True), ("true", True), ("TRUE", True), ("Yes", True), ("on", True),
    ("0", False), ("false", False), ("FALSE", False), ("no", False), ("off", False),
    ("", False), ("random", False),
])
def test_env_bool(monkeypatch, raw, expected):
    """"false" 是 truthy string —— 直接 bool() 會全部變 True。

    這曾是 SESSION_COOKIE_SECURE 類設定最常見的安全性錯誤來源。
    """
    monkeypatch.setenv("SIPH_TEST_FLAG", raw)
    assert _env_bool("SIPH_TEST_FLAG", default=False) is expected


def test_env_bool_uses_default_when_unset(monkeypatch):
    monkeypatch.delenv("SIPH_TEST_FLAG", raising=False)
    assert _env_bool("SIPH_TEST_FLAG", default=True) is True


# ----------------------------------------------------------------------
# DATABASE_URL 正規化
# ----------------------------------------------------------------------
@pytest.mark.parametrize("raw,expected_prefix", [
    ("postgres://u:p@h/db", "postgresql+psycopg://"),
    ("postgresql://u:p@h/db", "postgresql+psycopg://"),
    ("postgresql+psycopg://u:p@h/db", "postgresql+psycopg://"),
])
def test_normalize_database_url_upgrades_scheme(raw, expected_prefix):
    """SQLAlchemy 2.x 不接受 postgres:// scheme。

    若不在 config 收斂，錯誤會延後到第一次 DB 查詢才爆。
    """
    assert _normalize_database_url(raw).startswith(expected_prefix)


def test_normalize_database_url_leaves_sqlite_alone():
    url = "sqlite:///instance/siph_lab.db"
    assert _normalize_database_url(url) == url


# ----------------------------------------------------------------------
# 安全預設值
# ----------------------------------------------------------------------
def test_secure_defaults():
    """安全相關預設一律採「安全的那一端」。"""
    assert BaseConfig.SESSION_COOKIE_HTTPONLY is True
    assert BaseConfig.SESSION_COOKIE_SAMESITE == "Lax"
    assert BaseConfig.WTF_CSRF_ENABLED is True
    assert BaseConfig.SQLALCHEMY_TRACK_MODIFICATIONS is False


def test_upload_allowlist_excludes_dangerous_types():
    """SAI §16：不接受一般 admin 任意 SVG。"""
    assert "svg" not in BaseConfig.ALLOWED_IMAGE_EXTENSIONS
    for ext in ("php", "html", "js", "gif", "exe"):
        assert ext not in BaseConfig.ALLOWED_IMAGE_EXTENSIONS
    assert BaseConfig.ALLOWED_IMAGE_EXTENSIONS == frozenset({"jpg", "jpeg", "png", "webp"})


def test_default_upload_limit_is_8mb():
    """SAI §16 建議 8 MB 上限。"""
    assert BaseConfig.MAX_CONTENT_LENGTH == 8 * 1024 * 1024


def test_session_lifetime_default_is_8_hours():
    """SAI §11.1 建議 8 小時 absolute session lifetime。"""
    assert BaseConfig.PERMANENT_SESSION_LIFETIME_HOURS == 8


def test_llms_txt_disabled_by_default():
    """SAI §13.2：llms.txt 是實驗性相容層，預設關閉。"""
    assert BaseConfig.ENABLE_LLMS_TXT is False


# ----------------------------------------------------------------------
# get_config
# ----------------------------------------------------------------------
@pytest.mark.parametrize("name,expected", [
    ("local", LocalConfig), ("development", LocalConfig),
    ("test", TestConfig), ("testing", TestConfig),
    ("production", ProductionConfig),
    ("LOCAL", LocalConfig), ("  Production  ", ProductionConfig),
])
def test_get_config_by_name(name, expected):
    assert get_config(name) is expected


def test_get_config_unknown_falls_back_to_local():
    """未知名稱退回 LocalConfig（最保守的設定）。

    注意這是刻意的取捨：production 的識別字必須精確，
    打錯字會靜默使用 LocalConfig 而不觸發 fail-fast 檢查。
    部署腳本必須明確設 APP_ENV=production。
    """
    assert get_config("typo-env") is LocalConfig


def test_get_config_reads_app_env(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    assert get_config(None) is ProductionConfig


# ----------------------------------------------------------------------
# ProductionConfig fail-fast（SAI §11.1、§21.4）
# ----------------------------------------------------------------------
class _FakeApp:
    """最小的 app 替身：init_app 只讀寫 config dict。"""

    def __init__(self, **config):
        self.config = {
            "SECRET_KEY": "a-very-long-production-secret-value",
            "SQLALCHEMY_DATABASE_URI": "postgresql+psycopg://u:p@h/db",
            "PUBLIC_BASE_URL": "https://siph-lab.ntust.edu.tw",
            "STORAGE_BACKEND": "gcs",
        }
        self.config.update(config)


def test_production_config_accepts_valid_settings():
    ProductionConfig.init_app(_FakeApp())  # 不得拋錯


@pytest.mark.parametrize("missing", ["SECRET_KEY", "SQLALCHEMY_DATABASE_URI", "PUBLIC_BASE_URL"])
def test_production_rejects_missing_required_settings(missing):
    """缺必要設定必須啟動失敗，而不是用預設值上線。"""
    with pytest.raises(RuntimeError, match="缺少必要設定"):
        ProductionConfig.init_app(_FakeApp(**{missing: ""}))


def test_production_rejects_localhost_base_url():
    """PUBLIC_BASE_URL 忘了改是 cutover 最常見的疏漏。"""
    with pytest.raises(RuntimeError, match="PUBLIC_BASE_URL"):
        ProductionConfig.init_app(_FakeApp(PUBLIC_BASE_URL="http://localhost:8000"))


def test_production_rejects_sqlite():
    """ADR-004：正式環境禁止把 SQLite 當永久資料庫。"""
    with pytest.raises(RuntimeError, match="SQLite"):
        ProductionConfig.init_app(
            _FakeApp(SQLALCHEMY_DATABASE_URI="sqlite:////app/instance/siph_lab.db")
        )


def test_production_rejects_local_storage():
    """ADR-006：Cloud Run container filesystem 不具持久性 [S17]。"""
    with pytest.raises(RuntimeError, match="gcs"):
        ProductionConfig.init_app(_FakeApp(STORAGE_BACKEND="local"))


def test_production_cookie_secure_defaults_true():
    assert ProductionConfig.SESSION_COOKIE_SECURE is True


# ----------------------------------------------------------------------
# TestConfig
# ----------------------------------------------------------------------
def test_test_config_disables_csrf_and_limiter():
    """一般功能測試不必抓 token；但 SAI §20 要求兩者本身要被測到，
    因此 conftest 另有 csrf_app / limiter_app fixture 局部開啟。"""
    assert TestConfig.WTF_CSRF_ENABLED is False
    assert TestConfig.RATELIMIT_ENABLED is False
    assert TestConfig.TESTING is True
