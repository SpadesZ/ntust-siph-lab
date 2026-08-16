# ============================================================
# NTUST SiPh Lab - Media Upload & Processing Service
#
# 上下游：
#   blueprints/admin/routes.py（表單 FileStorage）
#       -> MediaService.save_image()
#       -> Pillow 驗證/重新編碼
#       -> StorageBackend.save()（local 或 gcs）
#       -> 回傳 object key -> 寫入 Person.photo_path /
#          ResearchOutput.hero_image_path / SiteSetting.*_path
#   scripts/sync_media_to_gcs.py -> MediaService.checksum_of()
#
# 檔案路徑：
#   app/services/media_service.py
#
# 建立日期：2026-08-14
# 最後重大修改：2026-08-14
# 版本：v1.0
#
# 模組定位與責任邊界：
#   實作 SAI §16 Media/Upload 規格與 §11.2 的 Malicious upload
#   對策。這是使用者上傳資料進入系統的唯一入口，因此也是
#   安全邊界。
#
#   責任邊界（不得做的事）：
#     - 不得直接寫檔案系統（一律經過 StorageBackend）。
#     - 不得信任 client 提供的 filename 或 Content-Type。
#     - 不得在此更新資料庫欄位（由 service/route 決定）。
#
# 輸入 -> 處理 -> 輸出 Pipeline：
#   FileStorage
#     -> 副檔名 allowlist 檢查
#     -> 大小檢查
#     -> Pillow 開啟並 verify()（確認真的是圖片）
#     -> 轉 RGB/RGBA、依用途縮放
#     -> 重新編碼（丟棄所有原始 metadata，含 EXIF GPS）
#     -> 產生 UUID object key
#     -> StorageBackend.save()
#     -> StoredObject（key/size/mime/checksum）
#
# 主要 Class / Function：
#   MediaError                      - 使用者可見的上傳錯誤
#   MediaService.save_image(...)    - 主要入口
#   MediaService.delete_image(key)  - 刪除（含引用檢查由呼叫端負責）
#   MediaService.is_allowed_filename(name)
#   MediaService.checksum_of(key)   - 讀回並計算 checksum（驗收用）
#
# 依賴套件：
#   Pillow（影像驗證與重新編碼）、werkzeug（FileStorage）、
#   app.storage
#
# 環境變數（透過 app.config）：
#   MAX_CONTENT_LENGTH        - 8 MB 上限
#   ALLOWED_IMAGE_EXTENSIONS  - jpg/jpeg/png/webp
#   ALLOWED_IMAGE_MIMETYPES
#
# 資料庫使用方式：
#   不直接存取。回傳的 object key 由呼叫端寫入對應欄位。
#
# Error Handling / Fallback：
#   所有可預期的失敗（副檔名不符、非圖片、過大）都拋 MediaError，
#   其訊息設計為「可直接顯示給管理員」。
#   非預期失敗（storage 故障）拋 StorageError，由 route 轉為
#   500 並記錄 log。兩者分開的理由：前者是使用者輸入問題
#   （應顯示在表單旁），後者是系統問題（應告警）。
#
# 特殊機制（安全性：重新編碼）：
#   即使副檔名與 MIME 都正確，檔案內容仍可能是偽裝的多型檔案
#   （例如同時是合法 GIF 又是合法 PHP/HTML）。本服務一律用
#   Pillow 解碼後「重新編碼輸出」，而不是直接存原始 bytes。
#   這會摧毀任何附加在檔案中的非影像資料，是 SAI §11.2
#   「圖片重新編碼」對策的實作。
#
#   同時，重新編碼會丟棄 EXIF —— 這不只是 §16「移除不必要
#   metadata」的要求，也是隱私保護：手機拍攝的人物照常含
#   GPS 座標。
#
# 已知限制與禁止事項：
#   1. 不接受 SVG。SAI §16 明定 svg 只接受可信內建 asset，
#      不接受一般 admin 任意上傳 —— SVG 可內嵌 <script>，
#      是儲存型 XSS 的常見載體。
#   2. 不產生多尺寸 responsive srcset 檔案集；改為單一
#      「合理最大寬」+ CSS 控制顯示尺寸。理由：研究室網站
#      圖片量少，多檔管理成本高於效益。width/height 屬性
#      仍會輸出以避免 CLS（SAI §18）。
#   3. 圖片會被完整讀進記憶體處理，受 MAX_CONTENT_LENGTH 約束。
#
# 維護契約：
#   1. 放寬 _ALLOWED_EXTENSIONS 前必須評估該格式能否被 Pillow
#      安全解碼，並更新 tests/test_media.py。
#   2. 不得為了「保留原圖品質」而略過重新編碼步驟。
#
# 驗證方式：
#   pytest tests/test_media.py
#   pytest tests/test_storage_backends.py
# ============================================================

from __future__ import annotations

import io
import logging
import uuid
from dataclasses import dataclass

from flask import current_app

from app.storage import get_storage
from app.storage.base import StoredObject

logger = logging.getLogger(__name__)

#: 各用途的目標最大尺寸（寬, 高）。
#: 人物照為方形頭像用途；研究圖片保留較大寬度以呈現量測圖細節。
_PROFILE_MAX = (800, 800)
_RESEARCH_MAX = (1600, 1600)
_SITE_MAX = (1920, 1920)

#: 允許解碼的最大像素數（寬 x 高）。
#:
#: 為什麼需要這個上限（交付前審查 REV-107 實測發現）：
#:   MAX_CONTENT_LENGTH 限制的是「壓縮後的位元組數」，不是解碼後的
#:   記憶體用量。PNG 對單色區域的壓縮率極高，因此一個遠低於 8 MB
#:   上限的檔案可以解出巨大的點陣圖：
#:
#:     12000 x 12000 = 144 MPx，編碼後僅 435 KB（通過 8 MB 檢查）
#:     -> 解碼佔用約 432 MB RSS
#:
#:   Pillow 自身的 MAX_IMAGE_PIXELS（89,478,485）在超過時「只發出
#:   DecompressionBombWarning」，要到兩倍（約 179 MPx）才會拋
#:   DecompressionBombError。也就是 89~179 MPx 之間完全沒有防護。
#:   Cloud Run 預設記憶體 512 MiB，單一請求即可觸發 OOM 並重啟
#:   instance —— 這是不需要任何憑證就能觸發的服務中斷。
#:
#:   40 MPx 的選擇：遠高於任何合理的人物照或量測圖
#:   （例如 8000 x 5000 = 40 MPx 已是高階全片幅相機的輸出），
#:   但把最壞情況的解碼記憶體壓在約 120 MB 以內。
_MAX_DECODED_PIXELS = 40_000_000

#: 用途 -> (object key 前綴, 最大尺寸)
_PURPOSES = {
    "people": ("people", _PROFILE_MAX),
    "research": ("research", _RESEARCH_MAX),
    "site": ("site", _SITE_MAX),
}

#: 輸出格式對照。統一輸出 WebP 以外的來源保留原格式家族，
#: 避免透明背景 PNG 被轉成 JPEG 後出現黑底。
_OUTPUT_FORMAT = {
    "JPEG": ("JPEG", "image/jpeg", "jpg"),
    "PNG": ("PNG", "image/png", "png"),
    "WEBP": ("WEBP", "image/webp", "webp"),
}


class MediaError(ValueError):
    """使用者輸入造成的上傳錯誤（訊息可直接顯示給管理員）。

    與 StorageError 分開的理由見檔頭 Error Handling。
    """


@dataclass(frozen=True)
class SavedMedia:
    """一次成功上傳的結果。"""

    key: str
    size: int
    content_type: str
    checksum_sha256: str
    width: int
    height: int

    @classmethod
    def from_stored(cls, stored: StoredObject, width: int, height: int) -> "SavedMedia":
        return cls(
            key=stored.key,
            size=stored.size,
            content_type=stored.content_type,
            checksum_sha256=stored.checksum_sha256,
            width=width,
            height=height,
        )


class MediaService:
    """圖片上傳、驗證與處理（SAI §16）。"""

    # ------------------------------------------------------------------
    # 檢查
    # ------------------------------------------------------------------
    @staticmethod
    def allowed_extensions() -> frozenset[str]:
        return current_app.config.get("ALLOWED_IMAGE_EXTENSIONS", frozenset())

    @staticmethod
    def is_allowed_filename(filename: str | None) -> bool:
        """副檔名是否在 allowlist 內。

        注意：這只是第一道檢查。真正的保證來自 Pillow 解碼成功
        （見檔頭「特殊機制」）—— 副檔名可以隨意偽造。
        """
        if not filename or "." not in filename:
            return False
        ext = filename.rsplit(".", 1)[1].strip().lower()
        return ext in MediaService.allowed_extensions()

    @staticmethod
    def _max_bytes() -> int:
        return int(current_app.config.get("MAX_CONTENT_LENGTH") or (8 * 1024 * 1024))

    # ------------------------------------------------------------------
    # 主要入口
    # ------------------------------------------------------------------
    @staticmethod
    def save_image(file_storage, purpose: str = "people") -> SavedMedia:
        """驗證、處理並儲存上傳的圖片。

        Args:
            file_storage: werkzeug FileStorage（來自表單）。
            purpose: people / research / site，決定 key 前綴與尺寸上限。

        Returns:
            SavedMedia（含 object key 與 checksum）。

        Raises:
            MediaError: 副檔名不允許、非有效圖片、超過大小上限。
            StorageError: 後端寫入失敗。
        """
        if purpose not in _PURPOSES:
            raise MediaError(f"未知的上傳用途：{purpose}")

        if file_storage is None or not getattr(file_storage, "filename", ""):
            raise MediaError("未選擇檔案。")

        filename = file_storage.filename
        if not MediaService.is_allowed_filename(filename):
            allowed = "、".join(sorted(MediaService.allowed_extensions()))
            raise MediaError(f"不支援的檔案類型。允許的格式：{allowed}（SAI §16）。")

        # 讀入記憶體。大小上限已由 Flask MAX_CONTENT_LENGTH 於
        # request 層擋下（超過會回 413），這裡是第二道防線，
        # 涵蓋非 HTTP 來源（例如 seed script 直接呼叫）。
        raw = file_storage.read()
        if not raw:
            raise MediaError("檔案內容為空。")

        max_bytes = MediaService._max_bytes()
        if len(raw) > max_bytes:
            raise MediaError(
                f"檔案過大（{len(raw) // 1024} KB），上限為 {max_bytes // 1024 // 1024} MB。"
            )

        processed, out_format, mime, ext, width, height = MediaService._process(
            raw, max_size=_PURPOSES[purpose][1]
        )

        # 伺服器生成檔名（SAI §16：不使用 user filename 作實際路徑）。
        prefix = _PURPOSES[purpose][0]
        key = f"{prefix}/{uuid.uuid4().hex}.{ext}"

        stored = get_storage().save(io.BytesIO(processed), key=key, content_type=mime)

        logger.info(
            "已儲存媒體 %s（%s, %dx%d, %d bytes, sha256=%s）",
            stored.key, out_format, width, height, stored.size, stored.short_checksum,
        )
        return SavedMedia.from_stored(stored, width=width, height=height)

    # ------------------------------------------------------------------
    # 影像處理
    # ------------------------------------------------------------------
    @staticmethod
    def _process(raw: bytes, max_size: tuple[int, int]):
        """驗證並重新編碼圖片。

        Returns:
            (處理後 bytes, 格式名, mime, 副檔名, 寬, 高)

        Raises:
            MediaError: 非有效影像或格式不受支援。

        為什麼要開兩次 Image.open：
          Pillow 的 verify() 會消耗檔案指標且之後不能再操作影像，
          官方建議「verify 之後必須重新開啟」。第一次用於確認
          檔案確實是可解析的影像（擋掉偽裝檔），第二次才做實際處理。
        """
        try:
            from PIL import Image, ImageOps
        except ImportError as exc:  # pragma: no cover - 相依已列入 requirements
            raise MediaError(
                "伺服器缺少影像處理套件 Pillow，無法處理上傳。"
            ) from exc

        # 第一次：驗證，並在「實際解碼之前」檢查尺寸。
        #
        # 順序很重要：Image.open 只讀 header，不會配置整張點陣圖，
        # 因此可以先用 probe.size 擋掉解壓炸彈，再決定要不要解碼。
        # 若等到 thumbnail() 才發現太大，記憶體已經配置出去了。
        try:
            with Image.open(io.BytesIO(raw)) as probe:
                width, height = probe.size
                pixels = width * height
                probe.verify()
        except MediaError:
            raise
        except Exception as exc:  # noqa: BLE001 - Pillow 會拋多種例外
            raise MediaError("檔案不是有效的影像，或影像已損毀。") from exc

        # 見 _MAX_DECODED_PIXELS 的說明（REV-107）。
        if pixels > _MAX_DECODED_PIXELS:
            raise MediaError(
                f"影像尺寸過大（{width}x{height}，約 {pixels // 1_000_000} 百萬像素）。"
                f"上限為 {_MAX_DECODED_PIXELS // 1_000_000} 百萬像素，"
                "請先縮小後再上傳（SAI §16）。"
            )

        # 第二次：實際處理。
        try:
            with Image.open(io.BytesIO(raw)) as image:
                source_format = (image.format or "").upper()
                if source_format not in _OUTPUT_FORMAT:
                    raise MediaError(
                        f"不支援的影像格式：{source_format or '未知'}（SAI §16）。"
                    )

                out_format, mime, ext = _OUTPUT_FORMAT[source_format]

                # 依 EXIF 方向旋轉後再丟棄 EXIF：
                # 若不先套用方向，手機直拍的照片會在移除 EXIF 後變成橫躺。
                image = ImageOps.exif_transpose(image)

                # 色彩模式正規化。P（調色盤）與 CMYK 直接存 JPEG/WebP
                # 會失敗或色偏。
                if out_format == "JPEG":
                    if image.mode not in ("RGB", "L"):
                        image = image.convert("RGB")
                else:
                    if image.mode not in ("RGB", "RGBA", "L"):
                        image = image.convert("RGBA")

                # 等比例縮放至上限內；小於上限則不放大。
                image.thumbnail(max_size, Image.Resampling.LANCZOS)
                width, height = image.size

                buffer = io.BytesIO()
                save_kwargs: dict = {}
                if out_format == "JPEG":
                    save_kwargs = {"quality": 85, "optimize": True, "progressive": True}
                elif out_format == "WEBP":
                    save_kwargs = {"quality": 85, "method": 4}
                elif out_format == "PNG":
                    save_kwargs = {"optimize": True}

                # 不傳入任何 exif/icc_profile 參數 => 輸出不含原始 metadata。
                image.save(buffer, format=out_format, **save_kwargs)
                return buffer.getvalue(), out_format, mime, ext, width, height

        except MediaError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise MediaError(f"影像處理失敗：{exc}") from exc

    # ------------------------------------------------------------------
    # 刪除與驗證
    # ------------------------------------------------------------------
    @staticmethod
    def delete_image(object_key: str | None) -> bool:
        """刪除媒體物件。

        注意：本方法「不檢查引用」。SAI §16 要求「被內容引用的
        檔案不可直接刪」，該檢查由呼叫端（admin route）在解除
        欄位引用之後才呼叫本方法 —— 因為只有呼叫端知道
        「這次操作是否已經把欄位清空」。
        """
        if not object_key:
            return False
        return get_storage().delete(object_key)

    @staticmethod
    def checksum_of(object_key: str) -> str | None:
        """讀回物件並計算 SHA-256（AC-24 媒體驗證用）。

        Returns:
            十六進位 checksum；物件不存在回 None。
        """
        import hashlib

        storage = get_storage()
        if not storage.exists(object_key):
            return None

        digest = hashlib.sha256()
        with storage.open(object_key) as stream:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def public_url(object_key: str | None) -> str | None:
        """object key -> 公開 URL；無 key 回 None。

        這是 template 取得圖片網址的唯一途徑（透過 Jinja global
        media_url），確保 template 不需要知道後端是 local 或 GCS。
        """
        if not object_key:
            return None
        try:
            return get_storage().public_url(object_key)
        except Exception:  # noqa: BLE001 - 壞 key 不應讓整頁失敗
            logger.warning("無法產生媒體 URL：%s", object_key)
            return None
