# ============================================================
# NTUST SiPh Lab - StorageBackend Interface
#
# 上下游：
#   config.STORAGE_BACKEND -> storage/__init__.get_storage(app)
#       -> LocalStorage | GcsStorage（皆實作本介面）
#   services/media_service.py -> StorageBackend.save/delete/public_url
#   templates -> media_url() Jinja global -> StorageBackend.public_url
#
# 檔案路徑：
#   app/storage/base.py
#
# 建立日期：2026-08-14
# 最後重大修改：2026-08-14
# 版本：v1.0
#
# 模組定位與責任邊界：
#   定義媒體儲存的抽象契約（SAI §10.4）。程式其餘部分「只」依賴
#   此介面，不得判斷目前是 local 還是 GCS。這是 ADR-006 能在
#   不改動 models/templates 的前提下切換到 Cloud Storage 的關鍵。
#
#   責任邊界（不得做的事）：
#     - 不得在此實作任何具體儲存邏輯。
#     - 不得 import flask request / current_app 以外的應用模組
#       （保持可獨立測試）。
#
# 輸入 -> 處理 -> 輸出 Pipeline：
#   save:       (file-like, object_key) -> 寫入後端 -> StoredObject
#   public_url: object_key -> 可公開存取的 URL 字串
#   delete:     object_key -> bool（是否確實刪除）
#   exists:     object_key -> bool
#   open:       object_key -> binary stream（備份與 checksum 用）
#
# 主要 Class：
#   StoredObject     - 儲存結果的不可變描述（key/size/mime/checksum）
#   StorageBackend   - 抽象基底類別
#   StorageError     - 統一的儲存例外型別
#
# 依賴套件：
#   標準庫 abc / dataclasses / hashlib / typing
#
# 環境變數：
#   由具體實作讀取（UPLOAD_DIR / GCS_BUCKET），本檔不讀。
#
# 資料庫使用方式：
#   無。DB 只保存 object_key、mime、size、alt、checksum
#   （SAI §10.4：不儲存 container 絕對路徑）。
#
# Error Handling / Fallback：
#   所有實作的失敗一律包成 StorageError 拋出，讓呼叫端有單一
#   例外型別可處理。禁止讓 botocore/google.cloud 的原生例外
#   洩漏到 service 層 —— 否則切換後端時 except 子句全部失效。
#
# 特殊機制：
#   checksum 使用 SHA-256，且在「寫入的同時」串流計算，
#   不需要二次讀檔。這支撐 SAI §10.6 備份 manifest 與
#   §21.3 media migration 的 checksum 驗證（AC-24）。
#
# 已知限制與禁止事項：
#   1. 介面刻意不提供 list_all()，因為 GCS 上大量列舉成本高；
#      備份腳本改由 DB 的 object_key 反查（DB 是唯一真實來源）。
#   2. 禁止在 object_key 中使用使用者提供的檔名
#      （SAI §16 Filename：伺服器生成 UUID/slug-safe 名稱）。
#   3. 禁止讓 object_key 以 "/" 開頭或包含 ".."。
#
# 維護契約：
#   新增後端時必須實作全部抽象方法，並在
#   tests/test_storage_backends.py 加入該後端的相同測試案例，
#   確保三者行為一致。
#
# 驗證方式：
#   pytest tests/test_storage_backends.py
# ============================================================

from __future__ import annotations

import hashlib
import posixpath
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import BinaryIO

#: 允許的 object key 形式：小寫英數、底線、連字號、點、斜線。
#: 刻意不允許空白與非 ASCII，確保在 GCS 與各種檔案系統行為一致。
_SAFE_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._/-]*$")

#: 串流讀取的區塊大小（1 MB）。
_CHUNK_SIZE = 1024 * 1024


class StorageError(RuntimeError):
    """所有儲存後端錯誤的統一型別。

    為什麼需要：
      呼叫端若要 except google.api_core.exceptions.GoogleAPIError，
      就等於把後端細節洩漏到 service 層，切換後端時會漏接例外。
      統一型別讓 media_service 只需要一個 except 子句。
    """


@dataclass(frozen=True)
class StoredObject:
    """一次成功儲存的結果描述。

    frozen=True 的理由：這是「已發生事實」的紀錄，
    不應在傳遞過程中被修改。
    """

    key: str
    size: int
    content_type: str
    checksum_sha256: str

    @property
    def short_checksum(self) -> str:
        """供 log 與 manifest 顯示的短雜湊。"""
        return self.checksum_sha256[:16]


def validate_object_key(key: str) -> str:
    """驗證並回傳正規化的 object key。

    為什麼要獨立成函式：
      所有後端都必須套用同一組規則，否則 local 可存但 GCS 失敗
      （或更糟：local 因為 ".." 而寫到 uploads 目錄外）。
      把規則收在基底模組，讓三個後端不可能各自為政。

    Raises:
        StorageError: key 為空、含路徑跳脫、或含不允許字元。
    """
    if not key:
        raise StorageError("object key 不得為空。")

    normalized = str(key).strip().lstrip("/")

    # 路徑跳脫防護（SAI §11.2 Malicious upload 的延伸）。
    if ".." in normalized.split("/"):
        raise StorageError(f"object key 不得包含路徑跳脫：{key!r}")

    # posixpath.normpath 會把 "a//b" 收斂為 "a/b"。
    normalized = posixpath.normpath(normalized)
    if normalized in {".", "/"} or normalized.startswith(("/", "..")):
        raise StorageError(f"不合法的 object key：{key!r}")

    if not _SAFE_KEY_PATTERN.match(normalized):
        raise StorageError(
            f"object key 只允許小寫英數與 . _ - / ：{key!r}"
        )

    return normalized


def stream_and_hash(stream: BinaryIO, sink) -> tuple[int, str]:
    """串流複製並同時計算 SHA-256。

    Args:
        stream: 來源二進位串流。
        sink: 具備 write(bytes) 的目標物件。

    Returns:
        (寫入位元組數, sha256 十六進位字串)

    為什麼一次完成兩件事：
      上傳檔案可能達 8 MB（SAI §16 上限）。若先寫檔再重讀算 checksum，
      等於兩倍 I/O；對 Cloud Storage 更意味著額外的下載費用。
    """
    digest = hashlib.sha256()
    total = 0
    while True:
        chunk = stream.read(_CHUNK_SIZE)
        if not chunk:
            break
        sink.write(chunk)
        digest.update(chunk)
        total += len(chunk)
    return total, digest.hexdigest()


class StorageBackend(ABC):
    """媒體儲存後端的抽象契約（SAI §10.4）。

    所有實作必須保證：
      1. save() 具冪等語意 —— 同一 key 重複寫入以最後一次為準。
      2. public_url() 對不存在的 key 仍回傳字串（不查詢後端），
         因為它會在 template render 時被大量呼叫，不能每次都打 API。
      3. delete() 對不存在的 key 回 False 而非拋錯。
    """

    #: 後端識別名稱，用於 log 與 /admin System health 顯示。
    name: str = "base"

    @abstractmethod
    def save(self, stream: BinaryIO, key: str, content_type: str) -> StoredObject:
        """儲存串流內容到指定 key。

        Raises:
            StorageError: key 不合法或寫入失敗。
        """

    @abstractmethod
    def delete(self, key: str) -> bool:
        """刪除物件。不存在時回 False，不拋錯。"""

    @abstractmethod
    def exists(self, key: str) -> bool:
        """物件是否存在。"""

    @abstractmethod
    def public_url(self, key: str) -> str:
        """回傳可公開存取的 URL。

        注意：本方法不應查詢後端（效能考量，見類別 docstring）。
        """

    @abstractmethod
    def open(self, key: str) -> BinaryIO:
        """開啟物件為二進位串流（備份/checksum 用）。

        Raises:
            StorageError: 物件不存在或讀取失敗。
        """

    @abstractmethod
    def health_check(self) -> tuple[bool, str]:
        """回傳 (是否可寫, 說明文字)，供 Admin System health 區塊使用。

        為什麼回 tuple 而非拋錯：
          這個方法會在 dashboard 每次載入時呼叫，
          它的目的是「顯示狀態」而不是「中斷流程」。
        """
