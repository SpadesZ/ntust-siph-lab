# ============================================================
# NTUST SiPh Lab - Media Sync to Cloud Storage
#
# 上下游：
#   uploads/（local backend）+ migration package 的 media_manifest.csv
#       -> 本腳本
#       -> app/storage/gcs.py（GcsStorage.save）
#       -> Cloud Storage bucket
#       -> checksum 比對報告（供 G4 存證）
#
# 檔案路徑：
#   scripts/sync_media_to_gcs.py
#
# 建立日期：2026-08-15
# 最後重大修改：2026-08-15
# 版本：v1.0
#
# 模組定位與責任邊界：
#   SAI §21.3 第 3 點指定的媒體同步工具，是 Production Migration
#   Gate G4（Media export）的執行者。§1.1 要求「人物/研究成果媒體
#   可由 local uploads 同步到 Cloud Storage，資料表只保存穩定
#   object key/URL metadata」——因此本腳本「不」修改資料庫：
#   object key 在兩個 backend 之間本來就相同，同步後 DB 不需異動。
#
#   責任邊界（不得做的事）：
#     - 不得修改資料庫（key 不變是設計前提，見上）。
#     - 不得刪除 GCS 上既有物件（此腳本只做上傳/驗證）。
#     - 不得在 checksum 不符時視為成功。
#
# 輸入 -> 處理 -> 輸出 Pipeline：
#   UPLOAD_DIR 掃描（或 package 的 media_manifest.csv）
#     -> 對每個 object key 計算本地 sha256
#     -> 若 GCS 已存在且 checksum 相同 -> 跳過（可重複執行）
#     -> 否則上傳並回讀驗證
#     -> 輸出同步報告（成功/跳過/失敗計數 + checksum 對照）
#
# 主要 Function：
#   sync_media(...)      - 主要進入點
#   _local_objects(...)  - 掃描本地媒體
#   _write_report(...)   - 產生 CSV 報告
#
# 依賴套件：
#   google-cloud-storage（選用相依，僅此腳本與 gcs backend 需要）
#
# 環境變數：
#   UPLOAD_DIR  - 本地媒體根目錄
#   GCS_BUCKET  - 目標 bucket（亦可用 --bucket 覆寫）
#   GOOGLE_APPLICATION_CREDENTIALS - 本機執行時的服務帳戶金鑰
#
# 資料庫使用方式：無（見責任邊界）。
#
# Error Handling / Fallback：
#   - 缺少 google-cloud-storage 時給出明確安裝指示。
#   - 單一物件失敗不中止整批；最後彙總並以非零 exit code 回報，
#     讓操作者一次看到所有問題而不是修一個跑一次。
#   - --dry-run 只比對不上傳，供 cutover 前預演。
#
# 特殊機制（冪等性）：
#   同步以 checksum 為準而非「檔案是否存在」。若 GCS 上已有同名
#   物件但內容不同（例如上一次同步中斷），會重新上傳而不是跳過。
#   這讓腳本可以安全地重複執行 —— cutover 當下最不需要的就是
#   「不確定跑到哪裡、不敢重跑」。
#
# 特殊機制（為什麼上傳後要回讀）：
#   blob.upload 成功只代表請求被接受。G4 要求「所有 local uploads
#   有 object key、checksum」，因此上傳後重新讀取 blob 的
#   md5/crc32c 或重新下載計算 sha256 來確認位元組確實一致。
#   本實作採「重新下載計算 sha256」，與本地端使用相同演算法，
#   避免 GCS 的 md5 在 composite object 情境下不可用的問題。
#
# 已知限制與禁止事項：
#   1. 逐一物件同步，不做並行。研究室網站的媒體數量在數百以內，
#      序列執行的可預測性比速度更重要。
#   2. 禁止用此腳本做「反向同步」（GCS -> local）。
#   3. 禁止在未確認 bucket IAM 的情況下執行；上傳成功但前台 403
#      是最常見的設定錯誤（見 deploy/cloudrun.md）。
#
# 維護契約：
#   若 MediaService 改變 object key 的組成規則，本腳本不需要修改
#   （key 直接來自檔案系統路徑）；但 deploy/cloudrun.md 的
#   bucket 結構說明必須同步更新。
#
# 驗證方式：
#   python scripts/sync_media_to_gcs.py --dry-run
#   python scripts/sync_media_to_gcs.py --bucket ntust-siph-lab-media
#   pytest tests/test_storage_backends.py
# ============================================================

from __future__ import annotations

import argparse
import csv
import hashlib
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class SyncError(RuntimeError):
    """同步過程的可預期錯誤。"""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _local_objects(upload_dir: Path) -> list[dict]:
    """掃描本地媒體，回傳 object key + checksum。

    object key 是相對於 UPLOAD_DIR 的 POSIX 路徑，
    與 LocalStorage / GcsStorage 使用的 key 完全一致 ——
    因此同步前後 DB 中的 photo_path 不需要任何異動。
    """
    if not upload_dir.is_dir():
        raise SyncError(f"找不到媒體目錄：{upload_dir}")

    objects: list[dict] = []
    for path in sorted(upload_dir.rglob("*")):
        if not path.is_file() or path.name.startswith(".") or path.suffix == ".tmp":
            continue
        objects.append(
            {
                "object_key": path.relative_to(upload_dir).as_posix(),
                "path": path,
                "size": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
        )
    return objects


def _guess_content_type(key: str) -> str:
    ext = key.rsplit(".", 1)[-1].lower() if "." in key else ""
    return {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "webp": "image/webp",
    }.get(ext, "application/octet-stream")


def sync_media(upload_dir: Path, bucket_name: str, dry_run: bool = False,
               report_path: Path | None = None) -> dict:
    """把本地媒體同步到 GCS，回傳統計摘要。"""
    try:
        from google.cloud import storage as gcs_storage
    except ImportError as exc:  # pragma: no cover - 依環境而定
        raise SyncError(
            "需要 google-cloud-storage：pip install google-cloud-storage"
        ) from exc

    objects = _local_objects(upload_dir)
    results: list[dict] = []
    uploaded = skipped = failed = 0

    client = None if dry_run else gcs_storage.Client()
    bucket = None if dry_run else client.bucket(bucket_name)

    for item in objects:
        key = item["object_key"]
        row = {
            "object_key": key,
            "size": item["size"],
            "local_sha256": item["sha256"],
            "remote_sha256": "",
            "action": "",
            "status": "",
        }

        try:
            if dry_run:
                row["action"] = "would-upload"
                row["status"] = "DRY-RUN"
                results.append(row)
                continue

            blob = bucket.blob(key)

            # 冪等性：已存在且內容相同就跳過（見檔頭特殊機制）。
            if blob.exists():
                existing = blob.download_as_bytes()
                existing_sum = _sha256_bytes(existing)
                if existing_sum == item["sha256"]:
                    row.update(action="skip", status="OK", remote_sha256=existing_sum)
                    skipped += 1
                    results.append(row)
                    continue
                row["action"] = "re-upload"
            else:
                row["action"] = "upload"

            with open(item["path"], "rb") as handle:
                blob.upload_from_file(
                    handle, content_type=_guess_content_type(key), size=item["size"]
                )

            # 上傳後回讀驗證（見檔頭特殊機制）。
            verify = _sha256_bytes(bucket.blob(key).download_as_bytes())
            row["remote_sha256"] = verify
            if verify != item["sha256"]:
                row["status"] = "CHECKSUM-MISMATCH"
                failed += 1
            else:
                row["status"] = "OK"
                uploaded += 1

        except Exception as exc:  # noqa: BLE001 - 單一物件失敗不中止整批
            row["status"] = f"ERROR: {exc}"
            failed += 1

        results.append(row)

    summary = {
        "bucket": bucket_name,
        "total": len(objects),
        "uploaded": uploaded,
        "skipped": skipped,
        "failed": failed,
        "results": results,
    }

    if report_path is not None:
        _write_report(report_path, summary)
    return summary


def _write_report(path: Path, summary: dict) -> None:
    """輸出 CSV 報告，供 G4 gate 存證（SAI §21.1）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["object_key", "size", "local_sha256", "remote_sha256",
                        "action", "status"],
        )
        writer.writeheader()
        writer.writerows(summary["results"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="把 local uploads 同步到 Cloud Storage（SAI §21.3、G4）。"
    )
    parser.add_argument("--bucket", default=None, help="GCS bucket；預設取 GCS_BUCKET。")
    parser.add_argument("--upload-dir", default=None, help="本地媒體目錄；預設取 UPLOAD_DIR。")
    parser.add_argument("--dry-run", action="store_true", help="只列出將上傳的物件，不實際上傳。")
    parser.add_argument("--report", default=None, help="CSV 報告輸出路徑。")
    args = parser.parse_args(argv)

    bucket = args.bucket or os.environ.get("GCS_BUCKET")
    if not bucket and not args.dry_run:
        print("需要 --bucket 或 GCS_BUCKET 環境變數。", file=sys.stderr)
        return 1

    upload_dir = Path(args.upload_dir or os.environ.get("UPLOAD_DIR") or "uploads").resolve()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report = Path(args.report) if args.report else Path(f"media-sync-report-{stamp}.csv")

    try:
        summary = sync_media(upload_dir, bucket or "(dry-run)", dry_run=args.dry_run,
                             report_path=report)
    except SyncError as exc:
        print(f"同步失敗：{exc}", file=sys.stderr)
        return 1

    print(f"媒體同步{'（DRY RUN）' if args.dry_run else ''}完成：{summary['bucket']}")
    print(f"  總數 {summary['total']}｜上傳 {summary['uploaded']}"
          f"｜已存在略過 {summary['skipped']}｜失敗 {summary['failed']}")
    print(f"  報告：{report}")

    for row in summary["results"]:
        if row["status"] not in ("OK", "DRY-RUN"):
            print(f"    ✗ {row['object_key']}：{row['status']}")

    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
