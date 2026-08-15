# ============================================================
# NTUST SiPh Lab - Google Cloud Storage Backend
#
# 上下游：
#   config.STORAGE_BACKEND=gcs -> storage/__init__.get_storage()
#       -> GcsStorage(bucket_name)
#   services/media_service.py -> GcsStorage.save/delete
#   scripts/sync_media_to_gcs.py -> GcsStorage.save + checksum 比對
#   Cloud Run service account -> GCS bucket IAM
#
# 檔案路徑：
#   app/storage/gcs.py
#
# 建立日期：2026-08-14
# 最後重大修改：2026-08-14
# 版本：v1.0
#
# 模組定位與責任邊界：
#   正式環境的媒體儲存（ADR-006 / SAI §21）。Cloud Run 的
#   container filesystem 不具持久性 [S17]，因此人物照與研究圖片
#   必須放 object storage。
#
#   責任邊界（不得做的事）：
#     - 不得在此決定 object key 命名（那是 media_service）。
#     - 不得在此處理 IAM/認證細節；一律依賴 Application Default
#       Credentials（Cloud Run 自動提供 service account）。
#
# 輸入 -> 處理 -> 輸出 Pipeline：
#   save: stream -> 讀入記憶體並計算 checksum -> blob.upload_from_string
#         -> StoredObject
#
# 主要 Class / Function：
#   GcsStorage.save / delete / exists / public_url / open / health_check
#   _require_client() - 延遲初始化 GCS client
#
# 依賴套件：
#   google-cloud-storage（選用相依：只有 STORAGE_BACKEND=gcs 才需要）
#
# 環境變數：
#   GCS_BUCKET          - bucket 名稱（必要）
#   GCS_PUBLIC_BASE_URL - 選用；若使用 CDN 或自訂網域則設定
#   GOOGLE_APPLICATION_CREDENTIALS - 本機測試用；Cloud Run 上不需要
#
# 資料庫使用方式：
#   無。DB 只保存 object key（SAI §10.4）。
#
# Error Handling / Fallback：
#   - google.cloud 例外一律轉為 StorageError，避免後端細節外洩。
#   - 套件未安裝時，錯誤訊息明確指出要安裝 google-cloud-storage，
#     而不是丟出難懂的 ImportError。
#
# 特殊機制（延遲初始化）：
#   client 在第一次實際使用時才建立，而非 __init__。
#   為什麼：讓 local 開發環境即使沒安裝 google-cloud-storage
#   也能 import 這個模組（例如跑 tests/test_storage_backends.py
#   的 local 部分）。若在 __init__ 就連線，import 就會失敗。
#
# 已知限制與禁止事項：
#   1. save() 會把整個檔案讀進記憶體。上限受 MAX_CONTENT_LENGTH
#      （8 MB）約束，對本案的人物照與研究圖片是可接受的取捨；
#      若未來要支援大型 dataset 上傳，必須改用 resumable upload。
#      這是已知限制，不是遺漏。
#   2. public_url 預設回傳 storage.googleapis.com 的公開 URL，
#      前提是 bucket 物件為公開讀取。若 bucket 為私有，
#      必須設定 GCS_PUBLIC_BASE_URL 指向 CDN/Load Balancer。
#   3. 禁止在此產生 signed URL 後存入 DB —— signed URL 會過期，
#      而 DB 應只保存穩定的 object key（SAI §1.1）。
#
# 維護契約：
#   1. 修改 public_url 規則時必須同步更新 deploy/cloudrun.md
#      的 bucket IAM 說明，否則會出現「上傳成功但前台 403」。
#   2. 本檔的行為必須與 LocalStorage 完全一致；
#      tests/test_storage_backends.py 以同一組案例驗證兩者。
#
# 驗證方式：
#   pytest tests/test_storage_backends.py -k gcs
#   （無 GCS 憑證時該組測試會自動 skip，並在報告中標示）
#   python scripts/smoke_cloud.py --check-storage
# ============================================================

from __future__ import annotations

import io
from typing import BinaryIO

from app.storage.base import (
    StorageBackend,
    StorageError,
    StoredObject,
    stream_and_hash,
    validate_object_key,
)


class GcsStorage(StorageBackend):
    """Google Cloud Storage 後端（ADR-006）。"""

    name = "gcs"

    def __init__(self, bucket_name: str, public_base_url: str | None = None) -> None:
        """
        Args:
            bucket_name: GCS bucket 名稱。
            public_base_url: 選用的公開存取基底（CDN / LB）。
                             未提供時使用 storage.googleapis.com。

        Raises:
            StorageError: bucket_name 未提供。
        """
        if not bucket_name:
            raise StorageError(
                "STORAGE_BACKEND=gcs 需要 GCS_BUCKET 環境變數（SAI §21.4）。"
            )
        self._bucket_name = bucket_name
        self._public_base_url = (public_base_url or "").rstrip("/") or None
        #: 延遲初始化，見檔頭「特殊機制」。
        self._client = None
        self._bucket = None

    # ------------------------------------------------------------------
    # 內部工具
    # ------------------------------------------------------------------
    def _require_bucket(self):
        """取得（必要時建立）bucket handle。

        為什麼把 import 放在函式內：
          google-cloud-storage 是選用相依。local 開發不安裝它時，
          `import app.storage.gcs` 仍必須成功，否則
          storage/__init__.py 的 backend 選擇邏輯無法載入。
        """
        if self._bucket is not None:
            return self._bucket

        try:
            from google.cloud import storage as gcs_storage  # 延遲 import
        except ImportError as exc:  # pragma: no cover - 依環境而定
            raise StorageError(
                "使用 STORAGE_BACKEND=gcs 需要安裝 google-cloud-storage："
                "pip install google-cloud-storage"
            ) from exc

        try:
            # 不傳 credentials：Cloud Run 會透過 Application Default
            # Credentials 自動提供 service account（SAI §21.2）。
            self._client = gcs_storage.Client()
            self._bucket = self._client.bucket(self._bucket_name)
        except Exception as exc:  # noqa: BLE001 - 統一轉為 StorageError
            raise StorageError(f"無法連線 GCS bucket {self._bucket_name!r}：{exc}") from exc

        return self._bucket

    # ------------------------------------------------------------------
    # StorageBackend 實作
    # ------------------------------------------------------------------
    def save(self, stream: BinaryIO, key: str, content_type: str) -> StoredObject:
        """上傳物件並回傳含 checksum 的描述。

        實作說明：
          先把串流讀進 BytesIO 以同時取得 checksum 與位元組內容。
          這裡的記憶體成本受 MAX_CONTENT_LENGTH 限制（見檔頭限制 1）。
        """
        safe_key = validate_object_key(key)
        bucket = self._require_bucket()

        buffer = io.BytesIO()
        size, checksum = stream_and_hash(stream, buffer)
        buffer.seek(0)

        try:
            blob = bucket.blob(safe_key)
            blob.upload_from_file(buffer, content_type=content_type, size=size)
        except Exception as exc:  # noqa: BLE001
            raise StorageError(f"上傳到 GCS 失敗：{safe_key!r}（{exc}）") from exc

        return StoredObject(
            key=safe_key,
            size=size,
            content_type=content_type,
            checksum_sha256=checksum,
        )

    def delete(self, key: str) -> bool:
        try:
            safe_key = validate_object_key(key)
        except StorageError:
            return False

        bucket = self._require_bucket()
        try:
            blob = bucket.blob(safe_key)
            if not blob.exists():
                return False
            blob.delete()
            return True
        except Exception as exc:  # noqa: BLE001
            raise StorageError(f"刪除 GCS 物件失敗：{safe_key!r}（{exc}）") from exc

    def exists(self, key: str) -> bool:
        try:
            safe_key = validate_object_key(key)
        except StorageError:
            return False
        try:
            return self._require_bucket().blob(safe_key).exists()
        except StorageError:
            raise
        except Exception:  # noqa: BLE001 - health 類查詢不應中斷頁面
            return False

    def public_url(self, key: str) -> str:
        """回傳公開 URL。不查詢後端（見 base.StorageBackend 契約）。"""
        safe_key = validate_object_key(key)
        if self._public_base_url:
            return f"{self._public_base_url}/{safe_key}"
        return f"https://storage.googleapis.com/{self._bucket_name}/{safe_key}"

    def open(self, key: str) -> BinaryIO:
        """下載物件並回傳記憶體串流。"""
        safe_key = validate_object_key(key)
        bucket = self._require_bucket()
        try:
            blob = bucket.blob(safe_key)
            data = blob.download_as_bytes()
        except Exception as exc:  # noqa: BLE001
            raise StorageError(f"讀取 GCS 物件失敗：{safe_key!r}（{exc}）") from exc
        return io.BytesIO(data)

    def health_check(self) -> tuple[bool, str]:
        """確認 bucket 可存取（Admin System health）。

        刻意只做 bucket.exists() 而不試寫：
          正式環境每次載入 dashboard 都寫一個探測物件會產生
          垃圾物件與費用。讀取權限正常即視為健康。
        """
        try:
            bucket = self._require_bucket()
            if bucket.exists():
                return True, f"GCS bucket 可存取（{self._bucket_name}）"
            return False, f"GCS bucket 不存在：{self._bucket_name}"
        except StorageError as exc:
            return False, str(exc)
        except Exception as exc:  # noqa: BLE001
            return False, f"GCS 健康檢查失敗：{exc}"
