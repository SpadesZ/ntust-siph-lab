# ============================================================
# NTUST SiPh Lab - Migration Package Importer (PostgreSQL)
#
# 上下游：
#   scripts/export_sqlite.py -> migration package
#       -> 本腳本
#       -> Cloud SQL PostgreSQL（schema 已由 flask db upgrade 建立）
#       -> scripts/verify_migration.py（獨立複驗）
#
# 檔案路徑：
#   scripts/import_postgres.py
#
# 建立日期：2026-08-15
# 最後重大修改：2026-08-15
# 版本：v1.0
#
# 模組定位與責任邊界：
#   SAI §10.5 / §21.3 指定的資料匯入工具，是 Production Migration
#   Gate G3 的後半段。§21.3 第 1 點明定「對 Cloud SQL 執行
#   flask db upgrade，禁止直接從 SQLite schema dump 建 PostgreSQL
#   schema」——因此本腳本假設目標 schema 已存在且為正確 revision，
#   只負責填入資料。
#
#   責任邊界（不得做的事）：
#     - 不得建立或修改 schema（DDL 屬於 Alembic）。
#     - 不得修改 alembic_version（那是 migration 狀態，不是資料）。
#     - 不得在 revision 不符時「盡力而為」地匯入。
#     - 不得靜默覆寫既有資料。
#
# 輸入 -> 處理 -> 輸出 Pipeline：
#   package 目錄 + 目標 DATABASE_URL
#     -> 讀 manifest，驗證 package_version
#     -> 比對目標 DB 的 alembic_version 與 manifest.schema_revision
#     -> 確認目標各表為空（或 --truncate 明確清空）
#     -> 依 manifest.table_order（FK 拓撲序）逐表插入
#     -> 還原型別（ISO8601 -> datetime）
#     -> 重設 PostgreSQL sequence（見特殊機制）
#     -> 匯入後驗證：row count / checksum / FK / unique slug /
#        relationship / 抽樣內容
#     -> 回傳非零 exit code 表示驗證失敗
#
# 主要 Function：
#   import_package(engine, package_dir, truncate) - 主要進入點
#   _verify_revision / _reset_sequences / _post_import_checks
#
# 依賴套件：
#   sqlalchemy、app（metadata）、psycopg（PostgreSQL 驅動）
#
# 環境變數：
#   DATABASE_URL - 目標資料庫（亦可用 --database-url 覆寫）
#
# 資料庫使用方式：
#   單一 transaction 內完成所有插入。任何一步失敗即整批 rollback，
#   不會留下「匯入到一半」的資料庫。
#
# Error Handling / Fallback：
#   - package_version 不符 -> 拒絕（格式可能已變更）。
#   - schema revision 不符 -> 拒絕，並明確指出兩邊的 revision。
#     這是 §10.5「只接受明確 schema revision」的落實：
#     revision 不符代表欄位可能增減，硬匯入會產生難以察覺的資料錯位。
#   - 目標表非空且未指定 --truncate -> 拒絕（避免主鍵衝突與混合資料）。
#   - 匯入後任一驗證失敗 -> rollback + 非零 exit code。
#
# 特殊機制（PostgreSQL sequence 重設）：
#   SQLite 的 INTEGER PRIMARY KEY 與 PostgreSQL 的 SERIAL/IDENTITY
#   行為不同。當我們帶著明確 id 值插入 PostgreSQL 時，
#   該表的 sequence 「不會」自動前進。若不處理，匯入後第一筆
#   由後台新增的資料會拿到 id=1 並立刻違反主鍵唯一約束 ——
#   而且這個錯誤在匯入當下不會出現，是上線後管理者新增內容
#   才爆炸的延遲性故障。因此匯入結束後必須把每個 sequence
#   設為 max(id)。這是 SQLite -> PostgreSQL 遷移最常見的坑
#   （SAI §24 列為「SQLite -> PostgreSQL 行為差異」高風險項）。
#
# 特殊機制（為什麼允許非 PostgreSQL 目標）：
#   --allow-non-postgres 讓同一套匯入邏輯可以匯入 SQLite，
#   供 tests/test_migration_roundtrip.py 在「沒有 PostgreSQL 的
#   CI 環境」也能演練完整 export -> import -> verify 流程。
#   正式遷移不得使用此旗標（預設關閉，且會顯示警告）。
#   這讓 round trip 的邏輯本身持續被測試，而不是等到上線當天
#   才第一次執行。
#
# 已知限制與禁止事項：
#   1. 整份 package 讀進記憶體（與 export 相同取捨）。
#   2. 禁止對「正在服務」的資料庫執行；應在 cutover 維護視窗進行。
#   3. 禁止以此腳本做增量同步；它只處理「一次性初始匯入」。
#
# 維護契約：
#   本腳本與 export_sqlite.py 必須成對修改。若 export 端新增了
#   型別正規化規則，這裡必須有對應的還原規則，否則會出現
#   「匯出成功、匯入成功、但內容悄悄改變」的最壞情況。
#
# 驗證方式：
#   python scripts/import_postgres.py --package ./migration-package --dry-run
#   pytest tests/test_migration_roundtrip.py
# ============================================================

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, func, select, text  # noqa: E402
from sqlalchemy.types import TypeDecorator  # noqa: E402

from scripts.export_sqlite import PACKAGE_VERSION, _table_checksum  # noqa: E402


class ImportError_(RuntimeError):
    """匯入過程的可預期錯誤（訊息直接面向操作者）。"""


# ----------------------------------------------------------------------
# 型別還原
# ----------------------------------------------------------------------
def _column_python_type(column):
    """取得欄位對應的 Python 型別，並穿透 TypeDecorator。

    為什麼不能直接用 isinstance(column.type, DateTime)：
      本專案的時間欄位使用 UtcDateTime，它是 TypeDecorator
      （impl = DateTime(timezone=True)），並不是 DateTime 的子類別。
      以 isinstance 判斷會全部漏掉，導致 ISO8601 字串原封不動
      送進 process_bind_param，而該方法會對字串取 .tzinfo 而失敗。

    為什麼也不能直接用 column.type.python_type：
      SQLAlchemy 的 TypeDecorator 並未把 python_type 委派給 impl，
      基底 TypeEngine.python_type 會直接 raise NotImplementedError。
      實測 UtcDateTime.python_type 正是如此，若只靠 try/except
      會靜默退回 None，所有時間欄位都不會被還原 —— 這個失敗
      是沉默的，只有在實際 INSERT 時才會以難懂的錯誤浮現。
      因此必須主動展開 impl 直到取得真正的 TypeEngine。
    """
    sa_type = column.type
    # 迴圈上限避免自訂型別互相包裝造成無限迴圈。
    for _ in range(10):
        if not isinstance(sa_type, TypeDecorator):
            break
        impl = sa_type.impl
        # impl 可能宣告為 class（DateTime）或 instance（DateTime(timezone=True)）。
        sa_type = impl() if isinstance(impl, type) else impl

    try:
        return sa_type.python_type
    except (NotImplementedError, AttributeError):
        return None


def _restore_value(value, column):
    """把 package 中的 JSON 值還原為欄位期望的 Python 型別。

    與 export_sqlite._normalise_value 成對（見檔頭維護契約）。
    ISO8601 字串必須還原成 timezone-aware datetime，否則
    PostgreSQL 的 timestamptz 會以連線時區解讀而整批偏移。
    """
    if value is None:
        return None

    py_type = _column_python_type(column)

    # datetime 必須先於 date 判斷：datetime 是 date 的子類別。
    if py_type is datetime and isinstance(value, str):
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    if py_type is date and isinstance(value, str):
        return date.fromisoformat(value)

    # SQLite 沒有原生 BOOLEAN，值以 0/1 取出；PostgreSQL 的
    # boolean 欄位需要真正的 bool。不轉換會在 Cloud SQL 匯入時
    # 出現型別錯誤，或（更糟）被靜默接受成非預期的值。
    if py_type is bool and isinstance(value, int) and not isinstance(value, bool):
        return bool(value)

    return value


# ----------------------------------------------------------------------
# 驗證
# ----------------------------------------------------------------------
def _verify_revision(conn, expected: str) -> None:
    """目標 DB 的 alembic_version 必須等於 package 綁定的 revision。"""
    try:
        rows = conn.execute(text("SELECT version_num FROM alembic_version")).fetchall()
    except Exception as exc:  # noqa: BLE001
        raise ImportError_(
            "目標資料庫沒有 alembic_version 表。\n"
            "請先執行 flask db upgrade 建立 schema（SAI §21.3 第 1 點）。"
        ) from exc

    if len(rows) != 1:
        raise ImportError_(f"目標 alembic_version 應恰好 1 筆，實際 {len(rows)} 筆。")

    actual = rows[0][0]
    if actual != expected:
        raise ImportError_(
            "schema revision 不符，拒絕匯入（SAI §10.5）。\n"
            f"  package 需要 : {expected}\n"
            f"  目標資料庫   : {actual}\n"
            "請把目標資料庫 upgrade 到相同 revision，或以相同 revision 重新匯出。"
        )


def _assert_tables_empty(conn, tables, truncate: bool) -> None:
    """確認目標表為空；--truncate 時反向清空。"""
    if truncate:
        # 反向順序刪除以滿足外鍵。
        for table in reversed(tables):
            conn.execute(table.delete())
        return

    non_empty = []
    for table in tables:
        count = conn.execute(select(func.count()).select_from(table)).scalar_one()
        if count:
            non_empty.append(f"{table.name}({count} 筆)")

    if non_empty:
        raise ImportError_(
            "目標資料庫已有資料，拒絕匯入以避免主鍵衝突與資料混合：\n  "
            + "、".join(non_empty)
            + "\n若確定要覆寫，請加上 --truncate（會先清空這些表）。"
        )


def _reset_sequences(conn, tables, dialect_name: str) -> list[str]:
    """把整數主鍵的 sequence 對齊 max(id)（見檔頭特殊機制）。"""
    if dialect_name != "postgresql":
        return []

    adjusted = []
    for table in tables:
        pk_cols = list(table.primary_key.columns)
        if len(pk_cols) != 1:
            continue
        pk = pk_cols[0]
        if not isinstance(pk.type.python_type, type) or pk.type.python_type is not int:
            continue

        # pg_get_serial_sequence 回 NULL 表示該欄不是 serial/identity。
        seq = conn.execute(
            text("SELECT pg_get_serial_sequence(:t, :c)"),
            {"t": table.name, "c": pk.name},
        ).scalar()
        if not seq:
            continue

        conn.execute(
            text(
                f'SELECT setval(:seq, COALESCE((SELECT MAX("{pk.name}") FROM "{table.name}"), 0) + 1, false)'
            ),
            {"seq": seq},
        )
        adjusted.append(f"{table.name}.{pk.name}")
    return adjusted


def _post_import_checks(conn, tables_by_name, manifest) -> list[str]:
    """匯入後驗證（SAI §10.5：FK、unique slug、row-count、relationship、抽樣內容）。"""
    problems: list[str] = []

    # 1. row count + 內容 checksum
    for meta in manifest["tables"]:
        table = tables_by_name[meta["name"]]
        count = conn.execute(select(func.count()).select_from(table)).scalar_one()
        if count != meta["row_count"]:
            problems.append(
                f"{meta['name']} 筆數不符：package {meta['row_count']}、匯入後 {count}"
            )
            continue

        # 以與 export 完全相同的讀取路徑與正規化規則重算 checksum，
        # 證明「內容」而不只是「筆數」一致。
        #
        # 必須用 SQLAlchemy 的 select() 而非 raw SQL：
        #   raw SQL 會繞過 UtcDateTime 的 process_result_value，
        #   時間欄位以資料庫原生表示法回傳（SQLite 是文字、
        #   PostgreSQL 是 datetime），算出的 checksum 自然對不上
        #   package。這個對稱性是跨資料庫比對唯一成立的前提。
        from scripts.export_sqlite import _export_table

        normalised = _export_table(conn, table, meta["columns"])
        actual = _table_checksum(normalised)
        if actual != meta["sha256"]:
            problems.append(
                f"{meta['name']} 內容 checksum 不符：\n"
                f"    package {meta['sha256']}\n    匯入後   {actual}"
            )

    # 2. unique slug
    for name in ("people", "research_outputs"):
        table = tables_by_name.get(name)
        if table is None or "slug" not in table.c:
            continue
        total = conn.execute(select(func.count()).select_from(table)).scalar_one()
        distinct = conn.execute(
            select(func.count(func.distinct(table.c.slug)))
        ).scalar_one()
        if total != distinct:
            problems.append(f"{name}.slug 不唯一（{total} 筆但只有 {distinct} 個相異值）")
        nulls = conn.execute(
            select(func.count()).select_from(table).where(table.c.slug.is_(None))
        ).scalar_one()
        if nulls:
            problems.append(f"{name}.slug 有 {nulls} 筆空值")

    # 3. relationship / 孤兒關聯
    link = tables_by_name.get("research_output_people")
    people = tables_by_name.get("people")
    outputs = tables_by_name.get("research_outputs")
    if link is not None and people is not None and outputs is not None:
        orphan_people = conn.execute(
            select(func.count()).select_from(link)
            .where(~link.c.person_id.in_(select(people.c.id)))
        ).scalar_one()
        orphan_outputs = conn.execute(
            select(func.count()).select_from(link)
            .where(~link.c.research_output_id.in_(select(outputs.c.id)))
        ).scalar_one()
        if orphan_people:
            problems.append(f"research_output_people 有 {orphan_people} 筆指向不存在的 person")
        if orphan_outputs:
            problems.append(f"research_output_people 有 {orphan_outputs} 筆指向不存在的成果")

    # 4. 抽樣內容：時間欄位必須完整且為 UTC-aware
    if people is not None:
        missing_ts = conn.execute(
            select(func.count()).select_from(people)
            .where(people.c.created_at.is_(None) | people.c.updated_at.is_(None))
        ).scalar_one()
        if missing_ts:
            problems.append(f"people 有 {missing_ts} 筆缺少 created_at/updated_at")

    return problems


# ----------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------
def import_package(engine, package_dir: Path, truncate: bool = False,
                   dry_run: bool = False) -> dict:
    """把 migration package 匯入指定 engine，回傳結果摘要。"""
    from app import create_app
    from app.extensions import db

    manifest_path = package_dir / "manifest.json"
    if not manifest_path.is_file():
        raise ImportError_(f"找不到 manifest.json：{manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    if manifest.get("package_version") != PACKAGE_VERSION:
        raise ImportError_(
            f"package 格式版本不符：需要 {PACKAGE_VERSION}，"
            f"實際 {manifest.get('package_version')}。"
        )

    app = create_app()
    with app.app_context():
        metadata = db.metadata
    tables_by_name = {t.name: t for t in metadata.sorted_tables}
    ordered = [tables_by_name[n] for n in manifest["table_order"]]

    summary = {"tables": {}, "sequences": [], "problems": []}

    with engine.begin() as conn:
        _verify_revision(conn, manifest["schema_revision"])
        _assert_tables_empty(conn, ordered, truncate)

        for meta in manifest["tables"]:
            table = tables_by_name[meta["name"]]
            payload = json.loads(
                (package_dir / meta["file"]).read_text(encoding="utf-8")
            )
            if not payload:
                summary["tables"][meta["name"]] = 0
                continue

            rows = [
                {col: _restore_value(row[col], table.c[col]) for col in meta["columns"]}
                for row in payload
            ]
            conn.execute(table.insert(), rows)
            summary["tables"][meta["name"]] = len(rows)

        summary["sequences"] = _reset_sequences(conn, ordered, engine.dialect.name)
        summary["problems"] = _post_import_checks(conn, tables_by_name, manifest)

        if summary["problems"]:
            raise ImportError_(
                "匯入後驗證失敗，已 rollback：\n  - " + "\n  - ".join(summary["problems"])
            )
        if dry_run:
            raise _DryRunComplete(summary)

    return summary


class _DryRunComplete(Exception):
    """內部訊號：dry-run 完成後觸發 rollback。"""

    def __init__(self, summary):
        super().__init__("dry run")
        self.summary = summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="把 migration package 匯入 PostgreSQL（SAI §10.5、§21.3）。"
    )
    parser.add_argument("--package", required=True, help="migration package 目錄。")
    parser.add_argument("--database-url", default=None,
                        help="目標連線字串。預設取自 DATABASE_URL。")
    parser.add_argument("--truncate", action="store_true",
                        help="匯入前清空目標表（危險；僅用於重跑遷移）。")
    parser.add_argument("--dry-run", action="store_true",
                        help="完整執行並驗證，但最後 rollback，不留下資料。")
    parser.add_argument("--allow-non-postgres", action="store_true",
                        help="允許匯入非 PostgreSQL 目標（僅供 round trip 測試）。")
    args = parser.parse_args(argv)

    import os

    url = args.database_url or os.environ.get("DATABASE_URL")
    if not url:
        print("需要 --database-url 或 DATABASE_URL 環境變數。", file=sys.stderr)
        return 1

    if not url.startswith("postgresql") and not args.allow_non_postgres:
        print(
            "目標不是 PostgreSQL。正式遷移的目標必須是 Cloud SQL PostgreSQL"
            "（ADR-005 / SAI §21）。\n"
            "若這是 round trip 演練，請明確加上 --allow-non-postgres。",
            file=sys.stderr,
        )
        return 1
    if args.allow_non_postgres and not url.startswith("postgresql"):
        print("警告：正在匯入非 PostgreSQL 目標，僅適用於演練與測試。\n")

    engine = create_engine(url)
    package_dir = Path(args.package).resolve()

    try:
        summary = import_package(engine, package_dir, truncate=args.truncate,
                                 dry_run=args.dry_run)
    except _DryRunComplete as done:
        print("Dry run 完成，所有驗證通過，已 rollback（未寫入任何資料）。")
        for name, count in done.summary["tables"].items():
            print(f"  {name:26s} {count:5d} 筆")
        return 0
    except ImportError_ as exc:
        print(f"匯入失敗：{exc}", file=sys.stderr)
        return 1

    print("匯入完成，所有驗證通過。")
    for name, count in summary["tables"].items():
        print(f"  {name:26s} {count:5d} 筆")
    if summary["sequences"]:
        print(f"  已重設 sequence：{', '.join(summary['sequences'])}")
    print("\n下一步：python scripts/sync_media_to_gcs.py 與 scripts/verify_migration.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
