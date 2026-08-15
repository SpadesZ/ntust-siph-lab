# ============================================================
# NTUST SiPh Lab - Database Portability Tests
#
# 上下游：
#   tests/conftest.py -> 本檔
#       -> app/repositories/*（查詢邊界）
#       -> migrations/（Alembic）
#       -> 選用的 PostgreSQL（TEST_POSTGRES_URL）
#
# 檔案路徑：tests/test_db_portability.py
# 建立日期：2026-08-14 / 版本：v1.0
#
# 模組定位（SAI §10.2 Database Portability Contract）：
#   1. 所有 CRUD 透過 ORM/repository，不在 route 中寫 raw SQL
#   2. 禁止依賴 SQLite-only function/PRAGMA 作為業務邏輯
#   3. constraint 必須在 migration 中明確定義
#   4. datetime 一律 UTC
#   5. Alembic 必須在 SQLite 與 PostgreSQL 都能 empty -> head
#
# 為什麼要「掃描原始碼」：
#   前三項是「程式碼撰寫方式」的約束，無法只靠執行期測試發現 ——
#   一段 SQLite-only 的 raw SQL 在本機會正常執行，
#   只有部署到 PostgreSQL 才會失敗。靜態掃描能在開發當下就攔截。
#
# PostgreSQL 測試：
#   設定環境變數 TEST_POSTGRES_URL 後才會執行；
#   未設定時自動 skip 並在報告中標示（不視為通過）。
#   例：TEST_POSTGRES_URL=postgresql+psycopg://user:pw@localhost/siph_test
#
# 驗證方式：
#   pytest tests/test_db_portability.py -v
#   TEST_POSTGRES_URL=... pytest tests/test_db_portability.py -m postgres
# ============================================================

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_DIR = PROJECT_ROOT / "app"


def python_sources(*subdirs: str) -> list[Path]:
    """取得指定子目錄下的所有 .py 檔。"""
    files: list[Path] = []
    for subdir in subdirs:
        files.extend((APP_DIR / subdir).rglob("*.py"))
    return files


# ----------------------------------------------------------------------
# 契約 1：不在 route/service 寫 raw SQL
# ----------------------------------------------------------------------
def test_no_raw_sql_in_routes_and_services():
    """SAI §10.2：所有一般 CRUD 透過 ORM / repository。

    允許的例外：
      - health_service 的 SELECT 1（明確標註為健康檢查）
      - migrations/（DDL 本來就是 SQL）
      - scripts/backup_sqlite.py（SQLite 專用工具，已隔離）
    """
    offenders: list[str] = []
    sql_pattern = re.compile(
        r"""(execute\(\s*["']|text\(\s*["'])(select|insert|update|delete|drop|alter)""",
        re.IGNORECASE,
    )

    for path in python_sources("blueprints", "services", "repositories", "models"):
        if path.name == "health_service.py":
            continue  # 見 docstring 的例外說明
        content = path.read_text(encoding="utf-8")
        for match in sql_pattern.finditer(content):
            line_no = content[: match.start()].count("\n") + 1
            offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{line_no}")

    assert not offenders, (
        "以下位置使用了 raw SQL，違反 SAI §10.2 可攜契約：\n  " + "\n  ".join(offenders)
    )


def test_health_service_raw_sql_is_only_select_one():
    """health_service 的唯一 raw SQL 必須是 SELECT 1。"""
    content = (APP_DIR / "services" / "health_service.py").read_text(encoding="utf-8")
    statements = re.findall(r'text\(\s*["\']([^"\']+)["\']', content)

    assert statements, "health_service 應包含健康檢查查詢"
    for statement in statements:
        assert statement.strip().upper() == "SELECT 1", (
            f"health_service 只允許 SELECT 1，發現：{statement}"
        )


# ----------------------------------------------------------------------
# 契約 2：SQLite-only 技巧必須隔離
# ----------------------------------------------------------------------
def test_no_pragma_outside_local_adapter():
    """SAI §10.2：PRAGMA 只能出現在 local adapter（extensions.py）。"""
    offenders: list[str] = []

    for path in python_sources("blueprints", "services", "repositories", "models", "storage"):
        content = path.read_text(encoding="utf-8")
        if "PRAGMA" in content.upper():
            offenders.append(str(path.relative_to(PROJECT_ROOT)))

    assert not offenders, (
        "PRAGMA 只允許出現在 app/extensions.py 的連線事件中，"
        f"以下檔案違反此規則：{offenders}"
    )


@pytest.mark.parametrize(
    "sqlite_only_function",
    ["strftime(", "julianday(", "group_concat(", "datetime('now'", "sqlite_version("],
)
def test_no_sqlite_only_functions(sqlite_only_function):
    """禁止使用 SQLite 專屬函式（PostgreSQL 沒有相同語意）。"""
    offenders: list[str] = []

    for path in python_sources("blueprints", "services", "repositories", "models"):
        content = path.read_text(encoding="utf-8")
        if sqlite_only_function in content:
            offenders.append(str(path.relative_to(PROJECT_ROOT)))

    assert not offenders, (
        f"使用了 SQLite 專屬函式 {sqlite_only_function}：{offenders}"
    )


def test_no_hardcoded_local_paths_in_application_code():
    """禁止在應用程式碼寫死本機路徑（Cloud Run 上不存在）。"""
    offenders: list[str] = []
    patterns = [r"/app/uploads", r"/app/instance", r"C:\\\\Users", r"\.\./uploads"]

    for path in python_sources("blueprints", "services", "repositories", "models", "storage"):
        content = path.read_text(encoding="utf-8")
        # 排除註解行 —— 說明文字可以提到路徑。
        code_lines = [
            line for line in content.splitlines()
            if not line.lstrip().startswith("#")
        ]
        code = "\n".join(code_lines)
        for pattern in patterns:
            if re.search(pattern, code):
                offenders.append(f"{path.relative_to(PROJECT_ROOT)} ({pattern})")

    assert not offenders, (
        "應用程式碼不得寫死本機路徑（SAI §23.1）：\n  " + "\n  ".join(offenders)
    )


def test_templates_do_not_assume_local_uploads():
    """SAI §23.1：template 不可假設 /uploads 一定是本機檔案。

    所有媒體 URL 都必須經過 media_url() 全域函式。
    """
    offenders: list[str] = []

    for path in (APP_DIR / "templates").rglob("*.html"):
        content = path.read_text(encoding="utf-8")
        # 找出直接寫死 /uploads/ 的 src 或 href。
        if re.search(r'(src|href)="[^"]*?/uploads/', content):
            offenders.append(str(path.relative_to(PROJECT_ROOT)))

    assert not offenders, (
        "template 必須使用 media_url() 取得媒體網址，不得寫死 /uploads/：" f"{offenders}"
    )


# ----------------------------------------------------------------------
# 契約 3：constraint 在 migration 中明確定義
# ----------------------------------------------------------------------
def test_migrations_define_constraints_explicitly():
    """SAI §10.2：unique、nullable、length、check 必須在 migration 明確定義。"""
    versions = list((PROJECT_ROOT / "migrations" / "versions").glob("*.py"))
    assert versions, "應至少有一個 migration 檔"

    combined = "\n".join(p.read_text(encoding="utf-8") for p in versions)

    assert "CheckConstraint" in combined, "migration 應包含 CheckConstraint"
    assert "unique=True" in combined or "UniqueConstraint" in combined
    assert "nullable=False" in combined
    assert "ForeignKeyConstraint" in combined or "ForeignKey" in combined


def test_no_native_enum_in_migrations():
    """migration 不得使用 native ENUM（SAI §10.2）。"""
    versions = (PROJECT_ROOT / "migrations" / "versions").glob("*.py")
    for path in versions:
        content = path.read_text(encoding="utf-8")
        assert "sa.Enum(" not in content, (
            f"{path.name} 使用了 native ENUM；請改用 String + CheckConstraint"
        )


# ----------------------------------------------------------------------
# 契約 4：UTC 時間
# ----------------------------------------------------------------------
def test_no_naive_utcnow_usage():
    """禁止使用 datetime.utcnow()（naive，會造成時區比較錯誤）。"""
    offenders: list[str] = []

    for path in APP_DIR.rglob("*.py"):
        content = path.read_text(encoding="utf-8")
        code = "\n".join(
            line for line in content.splitlines() if not line.lstrip().startswith("#")
        )
        if "datetime.utcnow()" in code:
            offenders.append(str(path.relative_to(PROJECT_ROOT)))

    assert not offenders, (
        "請使用 app.models.mixins.utcnow()（timezone-aware）取代 "
        f"datetime.utcnow()：{offenders}"
    )


def test_datetimes_are_aware_on_current_backend(app, sample_person):
    """從資料庫讀回的時間必須是 timezone-aware。

    這條測試會在 SQLite 與 PostgreSQL 上執行相同斷言 ——
    UtcDateTime TypeDecorator 的存在就是為了讓兩者行為一致。
    """
    from datetime import timezone

    from app.extensions import db
    from app.models.person import Person

    with app.app_context():
        person = db.session.get(Person, sample_person["id"])

        for field in ("created_at", "updated_at"):
            value = getattr(person, field)
            assert value.tzinfo is not None, f"{field} 必須是 timezone-aware"
            assert value.utcoffset() == timezone.utc.utcoffset(None), (
                f"{field} 必須以 UTC 回傳"
            )


def test_datetime_roundtrip_preserves_utc(app):
    """寫入非 UTC 時區的時間，讀回時必須是等值的 UTC。"""
    from datetime import datetime, timedelta, timezone

    from app.extensions import db
    from app.models.person import Person

    taipei = timezone(timedelta(hours=8))
    written = datetime(2026, 8, 15, 20, 30, 0, tzinfo=taipei)

    with app.app_context():
        person = Person(slug="tz-test", name_zh="時區測試")
        person.created_at = written
        person.updated_at = written
        db.session.add(person)
        db.session.commit()
        db.session.refresh(person)

        assert person.created_at.utcoffset() == timedelta(0)
        # 20:30 UTC+8 == 12:30 UTC
        assert person.created_at.hour == 12
        assert person.created_at == written


# ----------------------------------------------------------------------
# 契約 5：Alembic 在兩種資料庫都能 empty -> head
# ----------------------------------------------------------------------
def test_migration_upgrades_from_empty_on_sqlite(tmp_path):
    """SQLite：empty database -> head。"""
    from flask_migrate import upgrade
    from sqlalchemy import inspect

    from app import create_app
    from app.extensions import db

    db_path = tmp_path / "fresh.db"
    application = create_app(
        "test",
        config_overrides={
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path.as_posix()}",
            "UPLOAD_DIR": str(tmp_path / "uploads"),
        },
    )

    with application.app_context():
        upgrade(directory=str(PROJECT_ROOT / "migrations"))
        tables = set(inspect(db.engine).get_table_names())
        db.session.remove()
        db.engine.dispose()

    assert "people" in tables and "research_outputs" in tables


@pytest.mark.postgres
def test_migration_upgrades_from_empty_on_postgres():
    """PostgreSQL：empty database -> head（SAI §21.1 Gate G2）。

    需要 TEST_POSTGRES_URL；未設定時 skip。
    這條測試是 Production Migration Gate 的 G2「DB portability」
    的自動化版本。
    """
    postgres_url = os.environ.get("TEST_POSTGRES_URL")
    if not postgres_url:
        pytest.skip(
            "未設定 TEST_POSTGRES_URL；PostgreSQL 可攜性未驗證。"
            "上線前必須依 SAI §21.1 G2 執行此測試。"
        )

    from flask_migrate import upgrade
    from sqlalchemy import inspect, text

    from app import create_app
    from app.extensions import db

    application = create_app(
        "test", config_overrides={"SQLALCHEMY_DATABASE_URI": postgres_url}
    )

    with application.app_context():
        # 從全新 schema 開始。
        with db.engine.begin() as conn:
            conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))

        upgrade(directory=str(PROJECT_ROOT / "migrations"))

        tables = set(inspect(db.engine).get_table_names())
        for table in ("admin_users", "people", "research_outputs", "audit_logs"):
            assert table in tables, f"PostgreSQL 缺少資料表 {table}"

        db.session.remove()
        db.engine.dispose()


@pytest.mark.postgres
def test_core_crud_works_on_postgres():
    """PostgreSQL 上的核心 CRUD 與約束行為（SAI §21.1 G2）。"""
    postgres_url = os.environ.get("TEST_POSTGRES_URL")
    if not postgres_url:
        pytest.skip("未設定 TEST_POSTGRES_URL")

    from flask_migrate import upgrade
    from sqlalchemy import text

    from app import create_app
    from app.extensions import db
    from app.services.person_service import PersonService

    application = create_app(
        "test", config_overrides={"SQLALCHEMY_DATABASE_URI": postgres_url}
    )

    with application.app_context():
        with db.engine.begin() as conn:
            conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
        upgrade(directory=str(PROJECT_ROOT / "migrations"))

        person = PersonService.create(
            {"name_zh": "PG 測試", "status": "current", "research_focus_zh": "可攜性測試"}
        )
        PersonService.publish(person)

        assert person.id is not None
        assert person.created_at.tzinfo is not None

        db.session.remove()
        db.engine.dispose()


# ----------------------------------------------------------------------
# Repository 邊界
# ----------------------------------------------------------------------
def test_blueprints_do_not_build_queries_directly():
    """SAI §9.1/§9.4：blueprint 不得直接寫 select()。

    所有查詢必須經過 repositories，
    否則「只顯示 published」的規則會在新頁面被遺漏。
    """
    offenders: list[str] = []

    for path in (APP_DIR / "blueprints").rglob("routes.py"):
        content = path.read_text(encoding="utf-8")
        code = "\n".join(
            line for line in content.splitlines() if not line.lstrip().startswith("#")
        )
        # db.session.get(Model, pk) 是允許的（依主鍵取單筆，非查詢邏輯）。
        if re.search(r"\bselect\(", code):
            offenders.append(str(path.relative_to(PROJECT_ROOT)))

    assert not offenders, (
        "blueprint 不得直接建構查詢，請改用 repositories：" f"{offenders}"
    )


def test_repositories_do_not_commit():
    """repository 只讀不寫（交易邊界在 service）。"""
    offenders: list[str] = []

    for path in (APP_DIR / "repositories").rglob("*.py"):
        content = path.read_text(encoding="utf-8")
        code = "\n".join(
            line for line in content.splitlines() if not line.lstrip().startswith("#")
        )
        if "db.session.commit()" in code:
            offenders.append(str(path.relative_to(PROJECT_ROOT)))

    assert not offenders, f"repository 不得 commit：{offenders}"


def test_models_do_not_commit_except_singleton():
    """model 不得 commit（唯一例外：SiteSetting.get 的初次建立）。"""
    offenders: list[str] = []

    for path in (APP_DIR / "models").rglob("*.py"):
        if path.name == "site_setting.py":
            continue  # 已於該檔 docstring 說明的刻意例外
        content = path.read_text(encoding="utf-8")
        code = "\n".join(
            line for line in content.splitlines() if not line.lstrip().startswith("#")
        )
        if "db.session.commit()" in code:
            offenders.append(str(path.relative_to(PROJECT_ROOT)))

    assert not offenders, f"model 不得 commit：{offenders}"
