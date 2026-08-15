#!/usr/bin/env python
# ============================================================
# NTUST SiPh Lab - SQLite Backup & Restore Drill
#
# 上下游：
#   排程/人工 -> 本腳本
#       -> SQLite Online Backup API（一致快照）
#       -> uploads/ 檔案掃描與 checksum
#       -> backups/<timestamp>.zip + manifest.json
#   HealthService.last_backup() -> 讀取 backups/ 顯示於 Admin
#   --verify-latest -> 在暫存目錄還原並執行完整性檢查（AC-14）
#
# 檔案路徑：
#   scripts/backup_sqlite.py
#
# 建立日期：2026-08-14
# 最後重大修改：2026-08-14
# 版本：v1.0
#
# 模組定位與責任邊界：
#   實作 SAI §10.6「本機備份與 Restore Drill」四個步驟：
#     1. 使用 SQLite Online Backup API 建立一致 snapshot [S11]
#     2. 將 uploads 與 DB snapshot 一起產生 manifest/checksum
#     3. 定期在全新暫存目錄還原，執行 integrity 檢查
#     4. 正式上雲後仍保留作為開發內容快照
#
#   責任邊界（不得做的事）：
#     - 不得備份 PostgreSQL（那由 Cloud SQL 自動備份策略負責，
#       SAI §10.6 第 4 點）。偵測到非 SQLite 時直接拒絕執行。
#     - 不得修改來源資料庫或 uploads。
#     - 不得把備份寫入 container filesystem 當作長期保存 [S17]。
#
# 輸入 -> 處理 -> 輸出 Pipeline：
#   DATABASE_URL（SQLite 路徑）+ UPLOAD_DIR
#     -> sqlite3.Connection.backup() 產生一致快照
#     -> 掃描 uploads 計算每個檔案的 SHA-256
#     -> 打包成 backups/siph-lab-backup-<UTC timestamp>.zip
#     -> zip 內含 manifest.json（版本、schema revision、
#        row counts、checksums）
#
# 主要 Function：
#   create_backup(...)     - 建立備份
#   verify_backup(path)    - 還原演練 + integrity_check（AC-14）
#   _sqlite_path_from_url  - 從 DATABASE_URL 解析檔案路徑
#   _collect_upload_files  - 掃描 uploads 並計算 checksum
#
# 依賴套件：
#   標準庫 sqlite3 / zipfile / hashlib / json / tempfile
#   （刻意零第三方相依，確保在最小環境也能還原備份）
#
# 環境變數：
#   DATABASE_URL / UPLOAD_DIR / BACKUP_DIR（透過 app.config 或直接讀取）
#
# 資料庫使用方式：
#   以唯讀方式開啟來源 DB，透過 Online Backup API 複製。
#   不執行任何寫入。
#
# Error Handling / Fallback：
#   - 來源 DB 不存在 -> 明確錯誤訊息並回傳非零 exit code。
#   - 非 SQLite -> 拒絕執行並說明原因。
#   - 備份過程失敗 -> 刪除半成品 zip，不留下損毀的備份檔
#     （損毀的備份比沒有備份更危險：會讓人誤以為有備份）。
#
# 特殊機制（為什麼不直接複製 .db 檔）：
#   SAI §10.3 允許啟用 WAL 模式。在 WAL 下，資料庫的最新狀態
#   分散在 .db 與 .db-wal 兩個檔案中；直接用 cp 複製 .db
#   會得到「缺少最新交易」甚至「內部結構不一致」的檔案。
#   SQLite Online Backup API [S11] 會在複製過程中處理鎖定與
#   WAL 合併，保證得到可用的一致快照 —— 這正是 SAI §10.3
#   「備份時必須使用 SQLite-aware snapshot」的要求。
#
# 特殊機制（還原演練 AC-14）：
#   --verify-latest 會：
#     1. 解壓到全新的暫存目錄（不碰現有資料）
#     2. 執行 PRAGMA integrity_check
#     3. 比對 manifest 中的 row counts 與實際查詢結果
#     4. 比對每個 upload 檔案的 SHA-256
#     5. 確認 alembic schema revision 存在
#   任一項失敗即回傳非零 exit code。
#
# 已知限制與禁止事項：
#   1. 備份檔未加密。若備份要離開本機，必須另行加密
#      （SAI §11.2 Data loss 對策提到 off-host copy）。
#   2. 禁止把備份 zip commit 進 Git（.gitignore 已排除 backups/）。
#   3. 本腳本不做輪替刪除；舊備份由維運人員自行管理，
#      避免自動刪除造成不可回復的損失。
#
# 維護契約：
#   新增需要備份的資料類型（例如未來的附件目錄）時，
#   必須同時更新 create_backup 與 verify_backup，
#   否則還原演練會通過但實際遺漏資料。
#
# 驗證方式：
#   python scripts/backup_sqlite.py
#   python scripts/backup_sqlite.py --verify-latest
#   pytest tests/test_backup.py
# ============================================================

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

#: manifest 格式版本。還原時據此判斷相容性。
MANIFEST_VERSION = 1

#: 備份檔名前綴。
BACKUP_PREFIX = "siph-lab-backup"

#: zip 內的路徑配置。
DB_ARCNAME = "database/siph_lab.db"
UPLOADS_ARCPREFIX = "uploads"
MANIFEST_ARCNAME = "manifest.json"

#: 需要統計筆數的資料表（SAI §8 的 7 張核心表）。
COUNTED_TABLES = (
    "admin_users",
    "people",
    "research_outputs",
    "research_output_people",
    "site_settings",
    "redirects",
    "audit_logs",
)


def _sqlite_path_from_url(database_url: str) -> Path:
    """從 SQLAlchemy DATABASE_URL 解析 SQLite 檔案路徑。

    Raises:
        ValueError: 不是 SQLite 連線字串（見檔頭責任邊界）。
    """
    if not database_url.startswith("sqlite"):
        raise ValueError(
            "本腳本只支援 SQLite（開發/驗收資料庫）。\n"
            "正式環境的 PostgreSQL 備份由 Cloud SQL 自動備份策略負責"
            "（SAI §10.6 第 4 點），請勿以此腳本備份正式資料。"
        )

    # sqlite:////absolute/path 或 sqlite:///relative/path
    _, _, path_part = database_url.partition("///")
    if not path_part:
        raise ValueError(f"無法從連線字串解析路徑：{database_url}")

    # 去除可能的 query 參數。
    path_part = path_part.split("?", 1)[0]
    return Path(path_part).resolve()


def _sha256_file(path: Path) -> str:
    """計算檔案的 SHA-256（串流讀取，不受檔案大小限制）。"""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _collect_upload_files(upload_dir: Path) -> list[dict]:
    """掃描 uploads 目錄，回傳每個檔案的相對路徑、大小與 checksum。"""
    files: list[dict] = []
    if not upload_dir.is_dir():
        return files

    for path in sorted(upload_dir.rglob("*")):
        if not path.is_file():
            continue
        # 跳過健康檢查探測檔與暫存檔。
        if path.name.startswith(".") or path.suffix == ".tmp":
            continue
        relative = path.relative_to(upload_dir).as_posix()
        files.append(
            {
                "path": relative,
                "size": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
        )
    return files


def _snapshot_database(source: Path, destination: Path) -> None:
    """以 SQLite Online Backup API 建立一致快照 [S11]。

    見檔頭「特殊機制（為什麼不直接複製 .db 檔）」。
    """
    # 以唯讀模式開啟來源，確保不會意外修改正式資料。
    source_uri = f"file:{source.as_posix()}?mode=ro"
    src = sqlite3.connect(source_uri, uri=True)
    dst = sqlite3.connect(str(destination))
    try:
        # pages=0 表示一次複製完成；對本專案的資料量最有效率。
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def _read_row_counts(db_path: Path) -> dict[str, int]:
    """讀取各表筆數（供 manifest 與還原驗證比對）。"""
    counts: dict[str, int] = {}
    conn = sqlite3.connect(str(db_path))
    try:
        existing = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        for table in COUNTED_TABLES:
            if table in existing:
                counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()
    return counts


def _read_schema_revision(db_path: Path) -> str | None:
    """讀取 Alembic 的目前 schema revision。

    為什麼要記錄：
      還原時必須確認備份的 schema 版本與程式碼相容。
      SAI §10.5 要求 migration package 記錄 schema revision，
      備份也適用同一原則。
    """
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute("SELECT version_num FROM alembic_version LIMIT 1").fetchone()
        return row[0] if row else None
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def create_backup(
    database_url: str,
    upload_dir: Path,
    backup_dir: Path,
) -> Path:
    """建立包含資料庫快照與 uploads 的備份 zip。

    Returns:
        產生的備份檔路徑。

    Raises:
        FileNotFoundError: 來源資料庫不存在。
        ValueError: 非 SQLite 連線字串。
    """
    db_path = _sqlite_path_from_url(database_url)
    if not db_path.is_file():
        raise FileNotFoundError(
            f"找不到資料庫檔案：{db_path}\n請先執行 flask db upgrade。"
        )

    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive_path = backup_dir / f"{BACKUP_PREFIX}-{timestamp}.zip"

    # 在暫存目錄組裝，全部成功才移到 backups/ ——
    # 避免留下半成品 zip（見檔頭 Error Handling）。
    with tempfile.TemporaryDirectory(prefix="siph-backup-") as tmp:
        tmp_path = Path(tmp)
        snapshot = tmp_path / "siph_lab.db"

        _snapshot_database(db_path, snapshot)

        row_counts = _read_row_counts(snapshot)
        schema_revision = _read_schema_revision(snapshot)
        upload_files = _collect_upload_files(upload_dir)

        manifest = {
            "manifest_version": MANIFEST_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_database": str(db_path),
            "schema_revision": schema_revision,
            "database": {
                "arcname": DB_ARCNAME,
                "size": snapshot.stat().st_size,
                "sha256": _sha256_file(snapshot),
            },
            "row_counts": row_counts,
            "uploads": {
                "count": len(upload_files),
                "total_size": sum(f["size"] for f in upload_files),
                "files": upload_files,
            },
        }

        staging_archive = tmp_path / "archive.zip"
        with zipfile.ZipFile(staging_archive, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(snapshot, DB_ARCNAME)
            for entry in upload_files:
                zf.write(upload_dir / entry["path"], f"{UPLOADS_ARCPREFIX}/{entry['path']}")
            zf.writestr(
                MANIFEST_ARCNAME,
                json.dumps(manifest, ensure_ascii=False, indent=2),
            )

        shutil.move(str(staging_archive), str(archive_path))

    return archive_path


def _latest_backup(backup_dir: Path) -> Path | None:
    """取得最新的備份檔。"""
    if not backup_dir.is_dir():
        return None
    candidates = sorted(backup_dir.glob(f"{BACKUP_PREFIX}-*.zip"))
    return candidates[-1] if candidates else None


def verify_backup(archive_path: Path) -> list[str]:
    """還原演練（AC-14）。

    在全新暫存目錄還原並執行五項檢查（見檔頭「特殊機制」）。

    Returns:
        檢查結果訊息清單。

    Raises:
        AssertionError: 任一檢查失敗。
    """
    results: list[str] = []

    with tempfile.TemporaryDirectory(prefix="siph-restore-") as tmp:
        tmp_path = Path(tmp)

        with zipfile.ZipFile(archive_path) as zf:
            zf.extractall(tmp_path)

        manifest_file = tmp_path / MANIFEST_ARCNAME
        assert manifest_file.is_file(), "備份缺少 manifest.json"
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        results.append(
            f"manifest 版本 {manifest['manifest_version']}，"
            f"建立於 {manifest['created_at']}"
        )

        # --- 1. 資料庫檔案存在且 checksum 相符 ---
        restored_db = tmp_path / DB_ARCNAME
        assert restored_db.is_file(), "備份缺少資料庫檔案"
        actual_db_sum = _sha256_file(restored_db)
        assert actual_db_sum == manifest["database"]["sha256"], (
            f"資料庫 checksum 不符：預期 {manifest['database']['sha256']}，"
            f"實際 {actual_db_sum}"
        )
        results.append(f"資料庫 checksum 相符（{actual_db_sum[:16]}…）")

        # --- 2. SQLite integrity_check（AC-14 明列） ---
        conn = sqlite3.connect(str(restored_db))
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            assert integrity == "ok", f"integrity_check 失敗：{integrity}"
            results.append("PRAGMA integrity_check：ok")

            # --- 3. 外鍵完整性 ---
            fk_problems = conn.execute("PRAGMA foreign_key_check").fetchall()
            assert not fk_problems, f"外鍵完整性檢查失敗：{fk_problems}"
            results.append("PRAGMA foreign_key_check：無孤兒資料")
        finally:
            conn.close()

        # --- 4. row counts 相符 ---
        restored_counts = _read_row_counts(restored_db)
        for table, expected in manifest["row_counts"].items():
            actual = restored_counts.get(table)
            assert actual == expected, (
                f"資料表 {table} 筆數不符：預期 {expected}，實際 {actual}"
            )
        results.append(
            "資料筆數全部相符："
            + "、".join(f"{t}={c}" for t, c in sorted(manifest["row_counts"].items()))
        )

        # --- 5. schema revision 存在 ---
        revision = _read_schema_revision(restored_db)
        assert revision, "備份中缺少 alembic schema revision"
        assert revision == manifest["schema_revision"], (
            f"schema revision 不符：預期 {manifest['schema_revision']}，實際 {revision}"
        )
        results.append(f"schema revision：{revision}")

        # --- 6. uploads checksum 全部相符 ---
        uploads_root = tmp_path / UPLOADS_ARCPREFIX
        for entry in manifest["uploads"]["files"]:
            restored_file = uploads_root / entry["path"]
            assert restored_file.is_file(), f"備份缺少媒體檔案：{entry['path']}"
            actual = _sha256_file(restored_file)
            assert actual == entry["sha256"], (
                f"媒體檔案 checksum 不符：{entry['path']}\n"
                f"  預期 {entry['sha256']}\n  實際 {actual}"
            )
        results.append(
            f"媒體檔案 {manifest['uploads']['count']} 個，checksum 全部相符"
        )

    return results


def _resolve_settings() -> tuple[str, Path, Path]:
    """取得 DATABASE_URL、UPLOAD_DIR、BACKUP_DIR。

    優先使用 Flask app 設定（確保與應用完全一致），
    失敗時退回環境變數 —— 讓本腳本在應用無法啟動
    （正是最需要備份的情況）時仍可執行。
    """
    try:
        from app import create_app

        app = create_app(os.environ.get("APP_ENV", "local"))
        return (
            app.config["SQLALCHEMY_DATABASE_URI"],
            Path(app.config["UPLOAD_DIR"]),
            Path(app.config["BACKUP_DIR"]),
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[警告] 無法載入 Flask 設定（{exc}），改用環境變數。", file=sys.stderr)
        root = Path(__file__).resolve().parent.parent
        return (
            os.environ.get(
                "DATABASE_URL", f"sqlite:///{(root / 'instance' / 'siph_lab.db').as_posix()}"
            ),
            Path(os.environ.get("UPLOAD_DIR", root / "uploads")),
            Path(os.environ.get("BACKUP_DIR", root / "backups")),
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="NTUST SiPh Lab SQLite 備份與還原演練（SAI §10.6、AC-14）。"
    )
    parser.add_argument(
        "--verify-latest",
        action="store_true",
        help="對最新的備份執行還原演練（不建立新備份）。",
    )
    parser.add_argument(
        "--verify",
        metavar="ARCHIVE",
        help="對指定的備份檔執行還原演練。",
    )
    args = parser.parse_args()

    database_url, upload_dir, backup_dir = _resolve_settings()

    # --- 還原演練模式 ---
    if args.verify or args.verify_latest:
        archive = Path(args.verify) if args.verify else _latest_backup(backup_dir)
        if archive is None or not archive.is_file():
            print(f"找不到備份檔（目錄：{backup_dir}）。請先執行一次備份。", file=sys.stderr)
            return 1

        print(f"還原演練：{archive.name}")
        try:
            for line in verify_backup(archive):
                print(f"  ✔ {line}")
        except AssertionError as exc:
            print(f"  ✘ 還原演練失敗：{exc}", file=sys.stderr)
            return 1

        print("還原演練全部通過（AC-14）。")
        return 0

    # --- 建立備份模式 ---
    try:
        archive = create_backup(database_url, upload_dir, backup_dir)
    except (FileNotFoundError, ValueError) as exc:
        print(f"備份失敗：{exc}", file=sys.stderr)
        return 1

    size_kb = archive.stat().st_size // 1024
    print(f"備份完成：{archive}（{size_kb} KB）")
    print("建議接著執行還原演練：python scripts/backup_sqlite.py --verify-latest")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
