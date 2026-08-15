# ============================================================
# NTUST SiPh Lab - Migration Round Trip Tests
#
# 上下游：
#   tests/conftest.py（app fixture，已以 Alembic 建表）
#       -> scripts/export_sqlite.py
#       -> scripts/import_postgres.py
#       -> 全新的目標資料庫（SQLite，或設定 TEST_POSTGRES_URL 時用 PostgreSQL）
#
# 檔案路徑：tests/test_migration_roundtrip.py
# 建立日期：2026-08-15 / 版本：v1.0
#
# 模組定位（SAI §9.4 測試清單、§10.5、§20 Migration 層）：
#   驗證「export -> import -> 內容一致」這條路徑本身是對的。
#
#   為什麼這個測試特別重要：
#     遷移工具鏈只在 cutover 當天執行一次。若它有 bug，
#     發現的時機就是「正式資料已經搬到一半」的最糟時刻。
#     把 round trip 納入日常測試，等於每次 CI 都在演練 cutover。
#
#   為什麼預設用 SQLite 當目標：
#     讓沒有 PostgreSQL 的環境也能持續驗證「邏輯」是否正確。
#     型別轉換、checksum 對稱性、FK 順序、sequence 處理的
#     程式路徑完全相同。真正的 PostgreSQL 驗證由
#     TEST_POSTGRES_URL 觸發（Gate G2 要求上線前必跑）。
#
# 涵蓋：
#   1. empty -> head -> seed -> export -> import -> 內容逐表 checksum 相符
#   2. package 綁定 schema revision；revision 不符必須拒絕
#   3. 目標非空時必須拒絕（避免主鍵衝突與資料混合）
#   4. datetime 以 ISO8601 UTC 表示（跨 DB 可比對的前提）
#   5. 關聯與 slug 在匯入後仍完整
#
# 驗證方式：
#   pytest tests/test_migration_roundtrip.py -v
#   TEST_POSTGRES_URL=postgresql+psycopg://... pytest -m postgres
# ============================================================

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select

from scripts.export_sqlite import ExportError, build_package
from scripts.import_postgres import ImportError_, import_package


# ----------------------------------------------------------------------
# 輔助
# ----------------------------------------------------------------------
def _seeded_app(app):
    """在 app 中建立一組有關聯的內容，作為 round trip 的來源資料。"""
    from app.models.mixins import OutputType, PersonStatus
    from app.services.person_service import PersonService
    from app.services.research_service import ResearchService

    with app.app_context():
        person = PersonService.create(
            {
                "name_zh": "遷移測試成員",
                "name_en": "Migration Test Member",
                "status": PersonStatus.CURRENT,
                "research_focus_zh": "矽光子微環諧振器",
                "title_zh": "碩二生",
            }
        )
        PersonService.publish(person)

        output = ResearchService.create(
            {
                "output_type": OutputType.JOURNAL,
                "year": 2026,
                "title_zh": "遷移測試成果",
                "summary_zh": "用於驗證 export/import round trip 的成果資料，內容需足夠長以通過發布門檻檢查。",
                "method_zh": "FDTD 模擬。",
                "results_zh": "Q 值約 1.2 萬。",
                "venue": "Test Journal",
                "people": [person.id],
            }
        )
        ResearchService.publish(output)
        return {"person_id": person.id, "output_id": output.id, "slug": person.slug}


def _fresh_target(tmp_path: Path, name: str = "target.db") -> str:
    """建立一個「已由 Alembic upgrade 到 head」的空目標資料庫。

    刻意不用 create_all()：SAI §10.2 要求 migration 必須能從
    empty database 升到 head，用 create_all 會讓 migration 永遠不被驗證。
    """
    from flask_migrate import upgrade

    from app import create_app

    db_path = tmp_path / name
    url = f"sqlite:///{db_path.as_posix()}"

    target_app = create_app(
        "test",
        config_overrides={"SQLALCHEMY_DATABASE_URI": url},
    )
    with target_app.app_context():
        upgrade(directory=str(Path(__file__).resolve().parent.parent / "migrations"))
    return url


# ----------------------------------------------------------------------
# 1. 完整 round trip
# ----------------------------------------------------------------------
def test_round_trip_preserves_all_content(app, tmp_path):
    """export -> import 之後，每一張表的內容 checksum 都必須相符。"""
    seeded = _seeded_app(app)

    package_dir = tmp_path / "package"
    with app.app_context():
        manifest = build_package(
            package_dir,
            database_url=app.config["SQLALCHEMY_DATABASE_URI"],
            upload_dir=Path(app.config["UPLOAD_DIR"]),
        )

    assert manifest["total_rows"] > 0
    assert manifest["schema_revision"]

    target_url = _fresh_target(tmp_path)
    engine = create_engine(target_url)
    summary = import_package(engine, package_dir)

    # 每一張表的筆數都要對得上
    for meta in manifest["tables"]:
        assert summary["tables"][meta["name"]] == meta["row_count"]

    # import_package 內部已比對內容 checksum，若不符會 raise。
    # 這裡再獨立驗證關聯確實存在。
    from app.extensions import db

    with app.app_context():
        tables = {t.name: t for t in db.metadata.sorted_tables}

    with engine.connect() as conn:
        link_count = conn.execute(
            select(func.count()).select_from(tables["research_output_people"])
        ).scalar_one()
        assert link_count == 1, "人物與成果的關聯必須被保留"

        people_count = conn.execute(
            select(func.count()).select_from(tables["people"])
        ).scalar_one()
        assert people_count >= 1

        slug = conn.execute(
            select(tables["people"].c.slug).where(
                tables["people"].c.id == seeded["person_id"]
            )
        ).scalar_one()
        assert slug == seeded["slug"], "slug 必須逐字保留"

    engine.dispose()


def test_export_datetimes_are_iso8601_utc(app, tmp_path):
    """時間欄位必須以 ISO8601 UTC 匯出。

    這是跨資料庫 checksum 可比對的前提。若匯出的是 SQLite 的
    原生文字表示（'2026-08-15 05:47:04'），匯入 PostgreSQL 後
    重算必然不同，內容驗證就形同虛設。
    """
    _seeded_app(app)
    package_dir = tmp_path / "package"
    with app.app_context():
        build_package(
            package_dir,
            database_url=app.config["SQLALCHEMY_DATABASE_URI"],
            upload_dir=Path(app.config["UPLOAD_DIR"]),
        )

    rows = json.loads((package_dir / "tables" / "people.json").read_text(encoding="utf-8"))
    assert rows

    iso_utc = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?\+00:00$")
    for row in rows:
        for field in ("created_at", "updated_at"):
            assert iso_utc.match(row[field]), (
                f"{field} 不是 ISO8601 UTC：{row[field]!r}"
            )


def test_export_booleans_are_real_booleans(app, tmp_path):
    """SQLite 以 0/1 儲存 boolean；匯出必須是真正的 true/false。

    PostgreSQL 的 boolean 欄位不接受整數，若不轉換會在 Cloud SQL
    匯入時失敗（或更糟：被靜默接受成非預期的值）。
    """
    _seeded_app(app)
    package_dir = tmp_path / "package"
    with app.app_context():
        build_package(
            package_dir,
            database_url=app.config["SQLALCHEMY_DATABASE_URI"],
            upload_dir=Path(app.config["UPLOAD_DIR"]),
        )

    rows = json.loads((package_dir / "tables" / "people.json").read_text(encoding="utf-8"))
    for row in rows:
        assert isinstance(row["is_featured"], bool)


# ----------------------------------------------------------------------
# 2. Schema revision gate（SAI §10.5）
# ----------------------------------------------------------------------
def test_import_rejects_revision_mismatch(app, tmp_path):
    """package 的 schema revision 與目標不符時必須拒絕。"""
    _seeded_app(app)
    package_dir = tmp_path / "package"
    with app.app_context():
        build_package(
            package_dir,
            database_url=app.config["SQLALCHEMY_DATABASE_URI"],
            upload_dir=Path(app.config["UPLOAD_DIR"]),
        )

    # 竄改 manifest 的 revision，模擬「目標 DB 版本不同」。
    manifest_path = package_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["schema_revision"] = "deadbeef1234"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    engine = create_engine(_fresh_target(tmp_path))
    with pytest.raises(ImportError_, match="schema revision 不符"):
        import_package(engine, package_dir)
    engine.dispose()


def test_import_rejects_unknown_package_version(app, tmp_path):
    _seeded_app(app)
    package_dir = tmp_path / "package"
    with app.app_context():
        build_package(
            package_dir,
            database_url=app.config["SQLALCHEMY_DATABASE_URI"],
            upload_dir=Path(app.config["UPLOAD_DIR"]),
        )

    manifest_path = package_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["package_version"] = 999
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    engine = create_engine(_fresh_target(tmp_path))
    with pytest.raises(ImportError_, match="package 格式版本不符"):
        import_package(engine, package_dir)
    engine.dispose()


# ----------------------------------------------------------------------
# 3. 目標非空的保護
# ----------------------------------------------------------------------
def test_import_refuses_non_empty_target(app, tmp_path):
    """重複匯入必須被拒（避免主鍵衝突與資料混合）。"""
    _seeded_app(app)
    package_dir = tmp_path / "package"
    with app.app_context():
        build_package(
            package_dir,
            database_url=app.config["SQLALCHEMY_DATABASE_URI"],
            upload_dir=Path(app.config["UPLOAD_DIR"]),
        )

    engine = create_engine(_fresh_target(tmp_path))
    import_package(engine, package_dir)

    with pytest.raises(ImportError_, match="已有資料"):
        import_package(engine, package_dir)

    # --truncate 則允許重跑
    summary = import_package(engine, package_dir, truncate=True)
    assert summary["tables"]["people"] >= 1
    engine.dispose()


def test_dry_run_leaves_target_empty(app, tmp_path):
    """dry-run 必須完整驗證但不留下任何資料。"""
    from scripts.import_postgres import _DryRunComplete

    _seeded_app(app)
    package_dir = tmp_path / "package"
    with app.app_context():
        build_package(
            package_dir,
            database_url=app.config["SQLALCHEMY_DATABASE_URI"],
            upload_dir=Path(app.config["UPLOAD_DIR"]),
        )

    engine = create_engine(_fresh_target(tmp_path))
    with pytest.raises(_DryRunComplete):
        import_package(engine, package_dir, dry_run=True)

    from app.extensions import db

    with app.app_context():
        people_table = {t.name: t for t in db.metadata.sorted_tables}["people"]

    with engine.connect() as conn:
        count = conn.execute(select(func.count()).select_from(people_table)).scalar_one()
    assert count == 0, "dry-run 後目標必須仍是空的"
    engine.dispose()


# ----------------------------------------------------------------------
# 4. 匯出端的防呆
# ----------------------------------------------------------------------
def test_export_refuses_non_empty_output_dir(app, tmp_path):
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    (package_dir / "stale.json").write_text("{}", encoding="utf-8")

    with app.app_context():
        with pytest.raises(ExportError, match="已存在且非空"):
            build_package(
                package_dir,
                database_url=app.config["SQLALCHEMY_DATABASE_URI"],
                upload_dir=Path(app.config["UPLOAD_DIR"]),
            )


def test_export_refuses_non_sqlite_source(app, tmp_path):
    with app.app_context():
        with pytest.raises(ExportError, match="只支援從 SQLite 匯出"):
            build_package(
                tmp_path / "package",
                database_url="postgresql+psycopg://user:pw@localhost/db",
                upload_dir=Path(app.config["UPLOAD_DIR"]),
            )


# ----------------------------------------------------------------------
# 5. 真正的 PostgreSQL（Gate G2）
# ----------------------------------------------------------------------
@pytest.mark.postgres
@pytest.mark.skipif(
    not os.environ.get("TEST_POSTGRES_URL"),
    reason="未設定 TEST_POSTGRES_URL；PostgreSQL round trip 未驗證。"
    "上線前必須依 SAI §21.1 G2 執行此測試。",
)
def test_round_trip_to_postgres(app, tmp_path):
    """對真正的 PostgreSQL 執行 round trip，並驗證 sequence 已重設。

    sequence 是 SQLite -> PostgreSQL 最典型的陷阱：帶著明確 id
    插入時 sequence 不會前進，若不處理，上線後新增的第一筆資料
    會拿到 id=1 並違反主鍵約束。
    """
    from flask_migrate import upgrade

    from app import create_app

    _seeded_app(app)
    package_dir = tmp_path / "package"
    with app.app_context():
        build_package(
            package_dir,
            database_url=app.config["SQLALCHEMY_DATABASE_URI"],
            upload_dir=Path(app.config["UPLOAD_DIR"]),
        )

    pg_url = os.environ["TEST_POSTGRES_URL"]
    pg_app = create_app("test", config_overrides={"SQLALCHEMY_DATABASE_URI": pg_url})
    engine = create_engine(pg_url)

    # 乾淨的起點
    from app.extensions import db as _db

    with pg_app.app_context():
        _db.metadata.drop_all(engine)
        engine.dispose()
        engine = create_engine(pg_url)
        with engine.begin() as conn:
            conn.exec_driver_sql("DROP TABLE IF EXISTS alembic_version")
        upgrade(directory=str(Path(__file__).resolve().parent.parent / "migrations"))

    summary = import_package(engine, package_dir)
    assert summary["sequences"], "PostgreSQL 匯入後必須重設 sequence"

    # 驗證 sequence 真的可用：插入新列不得主鍵衝突。
    with pg_app.app_context():
        from app.models.mixins import PersonStatus
        from app.services.person_service import PersonService

        person = PersonService.create(
            {
                "name_zh": "序列測試",
                "status": PersonStatus.CURRENT,
                "research_focus_zh": "驗證 sequence 已對齊 max(id)",
            }
        )
        assert person.id is not None

    engine.dispose()
