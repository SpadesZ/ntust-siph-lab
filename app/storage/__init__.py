# ============================================================
# NTUST SiPh Lab - Storage Backend Factory
#
# 上下游：
#   create_app() -> init_storage(app) -> app.extensions['storage']
#   services/media_service.py -> get_storage() -> StorageBackend
#   templates -> media_url() Jinja global -> get_storage().public_url
#
# 檔案路徑：
#   app/storage/__init__.py
#
# 建立日期：2026-08-14
# 最後重大修改：2026-08-14
# 版本：v1.0
#
# 模組定位與責任邊界：
#   依 config.STORAGE_BACKEND 選擇具體後端，並把實例掛在
#   app.extensions 上。這是「環境差異只允許出現在 storage adapter」
#   （SAI §25）的實際落點：整個應用只有這個函式知道後端種類。
#
#   責任邊界（不得做的事）：
#     - 不得在此實作儲存邏輯。
#     - 不得在此讀 os.environ（值一律來自 app.config）。
#
# 輸入 -> 處理 -> 輸出 Pipeline：
#   輸入：app.config['STORAGE_BACKEND' | 'UPLOAD_DIR' | 'GCS_BUCKET']
#   處理：分派到 LocalStorage 或 GcsStorage
#   輸出：app.extensions['storage'] = StorageBackend 實例
#
# 主要 Function：
#   init_storage(app) - 建立並註冊後端
#   get_storage()     - 從 current_app 取得後端
#
# 依賴套件：
#   flask（current_app）、app.storage.local、app.storage.gcs
#
# 環境變數：
#   透過 config.py：STORAGE_BACKEND、UPLOAD_DIR、GCS_BUCKET、
#   GCS_PUBLIC_BASE_URL
#
# 資料庫使用方式：無。
#
# Error Handling / Fallback：
#   未知的 STORAGE_BACKEND 值直接 raise StorageError。
#   刻意不 fallback 到 local：若 production 把 backend 名稱打錯而
#   靜默退回 local，檔案會寫進 Cloud Run 的暫存檔案系統並在
#   instance 重啟後消失 [S17] —— 那是無聲的資料遺失，
#   遠比啟動失敗嚴重。
#
# 特殊機制：
#   使用 app.extensions dict 而非 module-level 全域變數，
#   讓同一個 process 可以建立多個 app（測試時常見）而不互相污染。
#
# 已知限制與禁止事項：
#   1. 禁止在 request 期間更換 backend。
#   2. 禁止讓 production 使用 local backend（ProductionConfig 已檢查）。
#
# 維護契約：
#   新增後端時：在 _BACKENDS 註冊、實作 base.StorageBackend 全部方法、
#   並在 tests/test_storage_backends.py 加入相同案例。
#
# 驗證方式：
#   pytest tests/test_storage_backends.py
# ============================================================

from __future__ import annotations

import logging

from flask import current_app

from app.storage.base import StorageBackend, StorageError, StoredObject  # noqa: F401
from app.storage.local import LocalStorage

logger = logging.getLogger(__name__)

#: app.extensions 中的 key。
_EXTENSION_KEY = "storage"


def _build_local(app) -> StorageBackend:
    """建立 LocalStorage。"""
    return LocalStorage(upload_dir=app.config["UPLOAD_DIR"])


def _build_gcs(app) -> StorageBackend:
    """建立 GcsStorage。

    延遲 import 的理由與 gcs.py 內部相同：讓沒安裝
    google-cloud-storage 的本機環境仍能載入本模組。
    """
    from app.storage.gcs import GcsStorage

    return GcsStorage(
        bucket_name=app.config.get("GCS_BUCKET"),
        public_base_url=app.config.get("GCS_PUBLIC_BASE_URL"),
    )


#: backend 名稱 -> 建構函式。
_BACKENDS = {
    "local": _build_local,
    "gcs": _build_gcs,
}


def init_storage(app) -> StorageBackend:
    """依 config 建立 storage backend 並註冊到 app.extensions。

    Raises:
        StorageError: STORAGE_BACKEND 值未知。
    """
    backend_name = (app.config.get("STORAGE_BACKEND") or "local").strip().lower()
    builder = _BACKENDS.get(backend_name)

    if builder is None:
        raise StorageError(
            f"未知的 STORAGE_BACKEND：{backend_name!r}。"
            f"可用值：{', '.join(sorted(_BACKENDS))}（SAI §10.4）。"
        )

    backend = builder(app)
    app.extensions[_EXTENSION_KEY] = backend
    logger.info("Storage backend 已初始化：%s", backend.name)
    return backend


def get_storage() -> StorageBackend:
    """從當前 app 取得 storage backend。

    Raises:
        StorageError: app 尚未初始化 storage（通常表示 create_app
                      被繞過，例如在沒有 app context 的腳本中呼叫）。
    """
    backend = current_app.extensions.get(_EXTENSION_KEY)
    if backend is None:
        raise StorageError(
            "Storage backend 尚未初始化。請確認在 app context 內使用，"
            "且 create_app() 已呼叫 init_storage()。"
        )
    return backend
