# ============================================================
# NTUST SiPh Lab - Local Filesystem Storage Backend
#
# 上下游：
#   config.STORAGE_BACKEND=local -> storage/__init__.get_storage()
#       -> LocalStorage(upload_dir)
#   services/media_service.py -> LocalStorage.save/delete
#   blueprints/public/routes.py -> /uploads/<path> 靜態服務
#   scripts/backup_sqlite.py、sync_media_to_gcs.py -> LocalStorage.open
#
# 檔案路徑：
#   app/storage/local.py
#
# 建立日期：2026-08-14
# 最後重大修改：2026-08-14
# 版本：v1.0
#
# 模組定位與責任邊界：
#   開發與內容驗收階段的媒體儲存（ADR-002 Local-first）。
#   把檔案寫在 uploads/ 之下，由 docker-compose bind mount 保存，
#   因此 container rebuild 後檔案不會消失（AC-13）。
#
#   責任邊界（不得做的事）：
#     - 不得被 production 使用。Cloud Run container filesystem
#       不具持久性 [S17]，ProductionConfig.init_app 已強制擋下。
#     - 不得決定 object key 的命名規則（那是 media_service）。
#
# 輸入 -> 處理 -> 輸出 Pipeline：
#   save: stream -> 驗證 key -> 建立父目錄 -> 寫入 .tmp
#         -> 計算 checksum -> 原子 rename -> StoredObject
#
# 主要 Class / Function：
#   LocalStorage.save / delete / exists / public_url / open / health_check
#   LocalStorage._resolve(key) - key 轉絕對路徑（含逃逸防護）
#
# 依賴套件：
#   標準庫 os / pathlib / shutil / tempfile
#
# 環境變數：
#   UPLOAD_DIR（由 config.py 讀取後傳入建構子，本檔不直接讀 env）。
#
# 資料庫使用方式：
#   無。DB 只保存 object key。
#
# Error Handling / Fallback：
#   - 所有 OSError 包成 StorageError。
#   - 寫入失敗時清除暫存檔，不留下半截檔案。
#   - delete 對不存在的檔案回 False。
#
# 特殊機制（原子性）：
#   採「先寫 .tmp 再 os.replace」的原子替換。
#   為什麼重要：若直接寫目標路徑，寫到一半發生錯誤或程序被中止，
#   會留下損毀的圖片，而 DB 仍指向它 —— 前台會顯示破圖且
#   checksum 驗證（AC-24）會失敗。os.replace 在同一檔案系統上
#   是原子操作，確保檔案「要嘛完整、要嘛不存在」。
#
# 已知限制與禁止事項：
#   1. 不支援多機共享；多個 Cloud Run instance 不可使用此後端。
#   2. public_url 回傳應用自身的 /uploads/<key> 路徑，
#      由 Flask 服務。正式環境應由 CDN/GCS 直接服務。
#   3. 禁止在 key 中使用使用者上傳的原始檔名（SAI §16）。
#
# 維護契約：
#   1. 修改 _resolve 的逃逸防護時必須同步更新
#      tests/test_storage_backends.py::test_path_traversal_rejected_by_local_backend。
#      這是防止任意檔案寫入的最後一道防線。
#   2. 若改變 public_url 的路徑前綴，必須同步修改
#      blueprints/public 的 uploads 路由與 nginx/CDN 設定。
#
# 驗證方式：
#   pytest tests/test_storage_backends.py
# ============================================================

from __future__ import annotations

import os
from pathlib import Path
from typing import BinaryIO

from app.storage.base import (
    StorageBackend,
    StorageError,
    StoredObject,
    stream_and_hash,
    validate_object_key,
)


class LocalStorage(StorageBackend):
    """把媒體寫入本機/掛載目錄的後端。"""

    name = "local"

    def __init__(self, upload_dir: str, url_prefix: str = "/uploads") -> None:
        """
        Args:
            upload_dir: uploads 根目錄的絕對或相對路徑。
            url_prefix: public_url 產生的路徑前綴。

        為什麼在建構子就 resolve 成絕對路徑：
          後續每次 _resolve 都要用它做「是否逃出根目錄」的比對。
          若保留相對路徑，程序的 cwd 改變會讓比對失效。
        """
        self._root = Path(upload_dir).resolve()
        self._url_prefix = "/" + url_prefix.strip("/")

    @property
    def root(self) -> Path:
        """uploads 根目錄（供備份腳本使用）。"""
        return self._root

    # ------------------------------------------------------------------
    # 內部工具
    # ------------------------------------------------------------------
    def _resolve(self, key: str) -> Path:
        """把 object key 轉成根目錄下的絕對路徑。

        雙重防護：
          1. validate_object_key 先擋掉 ".." 與不合法字元。
          2. 這裡再用 resolve() 後的 is_relative_to 確認結果確實
             落在根目錄內。

        為什麼要兩層：
          symlink 可以繞過純字串檢查 —— 例如 uploads/foo 是指向
          /etc 的 symlink 時，"foo/passwd" 通過了字元檢查，
          但實際路徑在根目錄外。resolve() 會展開 symlink，
          因此第二層檢查能擋下這種情況。
        """
        safe_key = validate_object_key(key)
        candidate = (self._root / safe_key).resolve()

        if not candidate.is_relative_to(self._root):
            raise StorageError(f"object key 解析後逃出 uploads 根目錄：{key!r}")

        return candidate

    # ------------------------------------------------------------------
    # StorageBackend 實作
    # ------------------------------------------------------------------
    def save(self, stream: BinaryIO, key: str, content_type: str) -> StoredObject:
        """原子地寫入檔案並回傳描述。"""
        target = self._resolve(key)
        temp_path = target.with_name(target.name + ".tmp")

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(temp_path, "wb") as sink:
                size, checksum = stream_and_hash(stream, sink)
                # 確保資料真的落到磁碟，而不是停留在 OS buffer。
                # 這對「備份/還原演練」的一致性很重要（SAI §10.6）。
                sink.flush()
                os.fsync(sink.fileno())

            # 原子替換：此步驟之後檔案必定完整。
            os.replace(temp_path, target)
        except OSError as exc:
            # 清理半截暫存檔，避免 uploads 目錄累積垃圾。
            try:
                if temp_path.exists():
                    temp_path.unlink()
            except OSError:
                pass
            raise StorageError(f"寫入本機檔案失敗：{key!r}（{exc}）") from exc

        return StoredObject(
            key=validate_object_key(key),
            size=size,
            content_type=content_type,
            checksum_sha256=checksum,
        )

    def delete(self, key: str) -> bool:
        """刪除檔案；不存在回 False。"""
        try:
            target = self._resolve(key)
        except StorageError:
            # key 不合法等同「這個東西不在我們的管理範圍」，回 False。
            return False

        try:
            target.unlink()
            return True
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise StorageError(f"刪除本機檔案失敗：{key!r}（{exc}）") from exc

    def exists(self, key: str) -> bool:
        try:
            return self._resolve(key).is_file()
        except StorageError:
            return False

    def public_url(self, key: str) -> str:
        """回傳 /uploads/<key>。

        刻意不檢查檔案是否存在：這個方法在每次 render 人物卡時
        都會被呼叫，加上 stat() 會讓 /members 頁產生大量 syscall。
        破圖問題改由 publish validator 與 Admin "Needs attention" 攔截。
        """
        safe_key = validate_object_key(key)
        return f"{self._url_prefix}/{safe_key}"

    def open(self, key: str) -> BinaryIO:
        """開啟檔案為二進位串流。"""
        target = self._resolve(key)
        try:
            return open(target, "rb")
        except OSError as exc:
            raise StorageError(f"讀取本機檔案失敗：{key!r}（{exc}）") from exc

    def health_check(self) -> tuple[bool, str]:
        """檢查 uploads 目錄是否可寫（Admin System health）。"""
        probe = self._root / ".healthz-write-probe"
        try:
            self._root.mkdir(parents=True, exist_ok=True)
            probe.write_bytes(b"ok")
            probe.unlink()
            return True, f"local uploads 可寫（{self._root}）"
        except OSError as exc:
            return False, f"local uploads 不可寫：{exc}"
