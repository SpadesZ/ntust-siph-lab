# ============================================================
# NTUST SiPh Lab - SQLite Migration Package Exporter
#
# 上下游：
#   本機 SQLite（開發/驗收資料庫）
#       -> 本腳本
#       -> migration package 目錄（manifest.json + tables/*.json
#          + media_manifest.csv）
#       -> scripts/import_postgres.py（匯入 Cloud SQL）
#       -> scripts/sync_media_to_gcs.py（媒體同步）
#       -> scripts/verify_migration.py（匯入後驗證）
#
# 檔案路徑：
#   scripts/export_sqlite.py
#
# 建立日期：2026-08-15
# 最後重大修改：2026-08-15
# 版本：v1.0
#
# 模組定位與責任邊界：
#   SAI §10.5 指定的「版本化 migration package」產生器，
#   是 Production Migration Gate G3（Data export）的唯一工具。
#   §21.3 明定「禁止直接從 SQLite schema dump 建 PostgreSQL
#   schema」——因此本腳本只匯出「資料」，不匯出任何 DDL。
#   目標 schema 一律由 `flask db upgrade` 以 Alembic 建立。
#
#   責任邊界（不得做的事）：
#     - 不得匯出 CREATE TABLE / DDL（schema 屬於 Alembic）。
#     - 不得連線 PostgreSQL 或任何雲端資源。
#     - 不得修改來源資料庫（唯讀開啟）。
#     - 不得把媒體「內容」放進 package（只放 manifest；
#       實際位元組由 sync_media_to_gcs.py 直接從 uploads 上傳，
#       避免產生數百 MB 的中間產物）。
#
# 輸入 -> 處理 -> 輸出 Pipeline：
#   DATABASE_URL(SQLite) + UPLOAD_DIR
#     -> 以唯讀 URI 開啟 SQLite
#     -> 讀取 alembic_version（package 綁定的 schema revision）
#     -> 依 metadata.sorted_tables 順序匯出每張表為 JSON
#        （FK 相依順序，供 import 端可依序插入）
#     -> 正規化型別（datetime -> ISO8601 UTC、bool -> true/false）
#     -> 計算每表 checksum 與 row count
#     -> 掃描 uploads 產生 media manifest（含 sha256）
#     -> 輸出 manifest.json
#
# 主要 Function：
#   build_package(output_dir)   - 主要進入點
#   _export_table(conn, table)  - 單表匯出與正規化
#   _normalise_value(value)     - 型別正規化（可攜性關鍵）
#   _table_checksum(rows)       - 內容 checksum
#
# 依賴套件：
#   標準庫 + app（取得 SQLAlchemy metadata 與 config）
#
# 環境變數：
#   DATABASE_URL - 來源 SQLite（必須是 sqlite://）
#   UPLOAD_DIR   - 媒體根目錄
#
# 資料庫使用方式：
#   唯讀。以 `file:...?mode=ro` URI 開啟，確保匯出過程
#   絕不可能修改開發資料。
#
# Error Handling / Fallback：
#   - 非 SQLite 連線字串直接拒絕（本腳本只負責「從 SQLite 匯出」）。
#   - alembic_version 缺失或多筆時拒絕：package 必須綁定
#     明確且唯一的 schema revision，否則 import 端無法驗證相容性。
#   - 輸出目錄已存在且非空時拒絕覆寫（避免混入舊 package 的殘留檔案
#     而產生「看起來成功但內容不一致」的 package）。
#
# 特殊機制（為什麼 checksum 要在「正規化之後」計算）：
#   SQLite 對 datetime 的儲存格式與 PostgreSQL 不同。若直接對
#   原始位元組計算 checksum，匯入 PostgreSQL 後再算必然不同，
#   verify_migration 就無法用 checksum 比對兩端資料是否一致。
#   因此 checksum 一律針對「正規化後的邏輯內容」計算，
#   讓同一份資料在兩種資料庫上得到相同的值（SAI §1.1
#   「可重複執行並有筆數、關聯、slug、時間欄位與 checksum 驗證」）。
#
# 特殊機制（表順序）：
#   使用 SQLAlchemy 的 metadata.sorted_tables，它會依外鍵相依
#   拓撲排序。import_postgres.py 直接依此順序插入即可滿足 FK，
#   不需要停用約束或事後修補。
#
# 已知限制與禁止事項：
#   1. 本腳本把整張表讀進記憶體。以研究室網站的資料量
#      （數百筆人物/成果）完全可接受；若未來成長到數十萬筆，
#      必須改為串流輸出。這是已知取捨，不是遺漏。
#   2. 禁止把 package commit 進 Git —— 內含人物 email 等個資
#      （.gitignore 已排除 migration-package-*）。
#   3. 禁止手動編輯 package 內容；那會讓 checksum 與實際資料
#      脫節，使 G3 的驗證失去意義。
#
# 維護契約：
#   新增資料表或欄位時不需要修改本腳本（表與欄位皆由 metadata
#   動態取得）。但若新增「非 JSON 可序列化」的欄位型別，
#   必須在 _normalise_value 增加對應處理，並同步更新
#   import_postgres.py 的還原邏輯 —— 兩者必須成對修改。
#
# 驗證方式：
#   python scripts/export_sqlite.py --output ./migration-package
#   pytest tests/test_migration_roundtrip.py
# ============================================================

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

#: package 格式版本。import 端據此判斷相容性。
PACKAGE_VERSION = 1

#: 不匯出的表。alembic_version 由 `flask db upgrade` 自行管理；
#: 把它當一般資料匯入會覆寫目標庫的實際 migration 狀態。
EXCLUDED_TABLES = frozenset({"alembic_version"})


class ExportError(RuntimeError):
    """匯出過程的可預期錯誤（訊息直接面向操作者）。"""


# ----------------------------------------------------------------------
# 型別正規化
# ----------------------------------------------------------------------
def _normalise_value(value):
    """把 SQLite 取出的值轉為「跨資料庫一致」的 JSON 可序列化型別。

    這是整個可攜契約的核心（SAI §10.2）。轉換規則：
      datetime -> ISO 8601 字串，一律標記為 UTC
      date     -> ISO 8601 日期
      bytes    -> 明確拒絕（本專案 schema 不應有 BLOB；
                  若出現代表 schema 偏離設計，應該讓匯出失敗
                  而不是靜默產生無法還原的內容）
      Decimal  -> str（避免 float 精度損失）

    為什麼 datetime 要強制標 UTC：
      SAI §8.9 規定所有 datetime 以 UTC 儲存。SQLite 沒有時區型別，
      取出的是 naive datetime。若不明確標記，import 端在
      PostgreSQL 的 timestamptz 欄位會以「本地時區」解讀，
      造成整批資料偏移數小時。
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        raise ExportError(
            "偵測到二進位欄位（BLOB）。本專案 schema 不應儲存二進位資料，"
            "媒體一律走 StorageBackend 只存 object key（SAI §10.4）。"
            "請確認 schema 是否偏離設計。"
        )
    return value


def _table_checksum(rows: list[dict]) -> str:
    """對「正規化後的內容」計算 checksum（見檔頭特殊機制）。

    以 sort_keys + 固定分隔符序列化，確保同樣的邏輯內容
    無論欄位順序如何都得到相同 checksum。
    """
    payload = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ----------------------------------------------------------------------
# 資料庫存取
# ----------------------------------------------------------------------
def _sqlite_path_from_url(database_url: str) -> Path:
    """從 DATABASE_URL 解析 SQLite 檔案路徑。"""
    if not database_url.startswith("sqlite"):
        raise ExportError(
            "export_sqlite.py 只支援從 SQLite 匯出（SAI §10.5）。\n"
            f"目前的 DATABASE_URL 是：{database_url.split('://')[0]}://…\n"
            "若要從 PostgreSQL 匯出，那屬於 Cloud SQL 的備份/匯出職責。"
        )
    _, _, path_part = database_url.partition("///")
    if not path_part:
        raise ExportError(f"無法從連線字串解析路徑：{database_url}")
    return Path(path_part.split("?", 1)[0]).resolve()


def _read_schema_revision(conn) -> str:
    """讀取並驗證 Alembic revision。

    為什麼要求「恰好一筆」：
      package 必須綁定唯一且明確的 schema revision，import 端
      才能拒絕 revision 不符的匯入（SAI §10.5：「import_postgres.py
      只接受明確 schema revision」）。多筆代表分支未合併，
      零筆代表資料庫從未跑過 migration ——兩者都不該進入正式遷移。
    """
    from sqlalchemy import text

    try:
        rows = conn.execute(text("SELECT version_num FROM alembic_version")).fetchall()
    except Exception as exc:  # noqa: BLE001
        raise ExportError(
            "來源資料庫沒有 alembic_version 表，代表 schema 不是由 Alembic 建立。\n"
            "請先執行：flask db upgrade（SAI §21.3 禁止以 schema dump 建表）。"
        ) from exc

    if len(rows) != 1:
        raise ExportError(
            f"alembic_version 應恰好有 1 筆，實際 {len(rows)} 筆。"
            "請先解決 migration 分支後再匯出。"
        )
    return rows[0][0]


def _export_table(conn, table, columns: list[str]) -> list[dict]:
    """匯出單張表，欄位順序固定、列順序穩定。

    為什麼透過 SQLAlchemy 而不是原生 sqlite3：
      SQLite 沒有原生 DATETIME 型別，原生 sqlite3 driver 會把
      時間欄位以「2026-08-15 05:47:04.010748」這種文字原樣回傳，
      _normalise_value 的 datetime 分支永遠不會觸發，
      匯出的就是 SQLite 專屬格式而非 ISO8601 UTC。
      那會造成兩個後果：(a) 匯入 PostgreSQL 時型別不符；
      (b) checksum 是對「SQLite 表示法」計算的，匯入後重算
      必然不同，G3 的內容驗證形同虛設。

      改用 SQLAlchemy 後，UtcDateTime TypeDecorator 的
      process_result_value 會把值還原成 timezone-aware datetime，
      與 import 端使用完全相同的型別處理路徑 —— 這個對稱性
      正是 checksum 能跨資料庫比對的前提。

    為什麼要固定排序：
      checksum 必須可重現。若列順序隨機，同一份資料兩次匯出
      會得到不同 checksum，G3 的驗證就失去意義。
    """
    from sqlalchemy import select

    ordering = [table.c[c] for c in columns]
    stmt = select(*[table.c[c] for c in columns]).order_by(*ordering)

    rows: list[dict] = []
    for record in conn.execute(stmt).mappings():
        rows.append({col: _normalise_value(record[col]) for col in columns})
    return rows


# ----------------------------------------------------------------------
# 媒體 manifest
# ----------------------------------------------------------------------
def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _build_media_manifest(upload_dir: Path) -> list[dict]:
    """掃描 uploads，產生 object key + checksum 清單（G4 的依據）。

    object key 就是相對於 UPLOAD_DIR 的 POSIX 路徑 ——
    與 LocalStorage/GcsStorage 使用的 key 完全一致，
    因此同一個 key 在本機與 GCS 都指向同一個邏輯物件。
    """
    entries: list[dict] = []
    if not upload_dir.is_dir():
        return entries

    for path in sorted(upload_dir.rglob("*")):
        if not path.is_file() or path.name.startswith(".") or path.suffix == ".tmp":
            continue
        entries.append(
            {
                "object_key": path.relative_to(upload_dir).as_posix(),
                "size": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
        )
    return entries


# ----------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------
def build_package(output_dir: Path, database_url: str | None = None,
                  upload_dir: Path | None = None) -> dict:
    """建立 migration package，回傳 manifest dict。

    Args:
        output_dir: package 輸出目錄（必須不存在或為空）。
        database_url: 覆寫來源；預設取自環境變數。
        upload_dir: 覆寫媒體目錄；預設取自環境變數。

    Returns:
        已寫入的 manifest 內容。
    """
    from sqlalchemy import create_engine

    from app import create_app
    from app.extensions import db

    app = create_app()
    with app.app_context():
        database_url = database_url or app.config["SQLALCHEMY_DATABASE_URI"]
        upload_dir = Path(upload_dir or app.config["UPLOAD_DIR"])
        # sorted_tables 依外鍵相依拓撲排序（見檔頭特殊機制）。
        ordered_tables = [
            table for table in db.metadata.sorted_tables
            if table.name not in EXCLUDED_TABLES
        ]
        table_columns = {t.name: [c.name for c in t.columns] for t in ordered_tables}

    db_path = _sqlite_path_from_url(database_url)
    if not db_path.is_file():
        raise ExportError(f"找不到來源資料庫：{db_path}")

    if output_dir.exists() and any(output_dir.iterdir()):
        raise ExportError(
            f"輸出目錄已存在且非空：{output_dir}\n"
            "請改用新目錄，或先清空 —— 混入舊 package 的殘留檔案會"
            "產生內容不一致但看似成功的 package。"
        )
    (output_dir / "tables").mkdir(parents=True, exist_ok=True)

    # 本腳本只發出 SELECT，不執行任何 DML/DDL（見檔頭責任邊界）。
    engine = create_engine(database_url)
    with engine.connect() as conn:
        revision = _read_schema_revision(conn)

        tables_meta: list[dict] = []
        for table in ordered_tables:
            columns = table_columns[table.name]
            rows = _export_table(conn, table, columns)

            target = output_dir / "tables" / f"{table.name}.json"
            target.write_text(
                json.dumps(rows, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            tables_meta.append(
                {
                    "name": table.name,
                    "columns": columns,
                    "row_count": len(rows),
                    "sha256": _table_checksum(rows),
                    "file": f"tables/{table.name}.json",
                }
            )
    engine.dispose()

    media = _build_media_manifest(upload_dir)
    media_csv = output_dir / "media_manifest.csv"
    with open(media_csv, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["object_key", "size", "sha256"])
        writer.writeheader()
        writer.writerows(media)

    manifest = {
        "package_version": PACKAGE_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "database": db_path.name,
            "backend": "sqlite",
        },
        "schema_revision": revision,
        "table_order": [t["name"] for t in tables_meta],
        "tables": tables_meta,
        "total_rows": sum(t["row_count"] for t in tables_meta),
        "media": {
            "count": len(media),
            "total_bytes": sum(m["size"] for m in media),
            "file": "media_manifest.csv",
        },
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="產生 SQLite -> PostgreSQL 的版本化 migration package（SAI §10.5）。"
    )
    parser.add_argument(
        "--output",
        default=None,
        help="輸出目錄。預設 ./migration-package-<UTC timestamp>。",
    )
    args = parser.parse_args(argv)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = Path(args.output or f"migration-package-{stamp}").resolve()

    try:
        manifest = build_package(output_dir)
    except ExportError as exc:
        print(f"匯出失敗：{exc}", file=sys.stderr)
        return 1

    print(f"Migration package 已產生：{output_dir}")
    print(f"  schema revision : {manifest['schema_revision']}")
    print(f"  資料表          : {len(manifest['tables'])} 張，共 {manifest['total_rows']} 筆")
    for table in manifest["tables"]:
        print(f"    - {table['name']:26s} {table['row_count']:5d} 筆  sha256={table['sha256'][:16]}…")
    print(f"  媒體物件        : {manifest['media']['count']} 個"
          f"（{manifest['media']['total_bytes'] // 1024} KB）")
    print("\n下一步（SAI §21.3）：")
    print("  1. 對 Cloud SQL 執行 flask db upgrade")
    print("  2. python scripts/import_postgres.py --package "
          f"{output_dir.name} --database-url <postgres-url>")
    print("  3. python scripts/sync_media_to_gcs.py --package "
          f"{output_dir.name} --bucket <gcs-bucket>")
    print("  4. python scripts/verify_migration.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
