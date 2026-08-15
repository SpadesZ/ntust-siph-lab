# ============================================================
# NTUST SiPh Lab - Storage Backend & Media Upload Tests
#
# 上下游：
#   tests/conftest.py -> 本檔
#       -> app/storage/base.py（介面契約與 key 驗證）
#       -> app/storage/local.py（本機後端）
#       -> app/storage/gcs.py（Cloud Storage 後端，無憑證時 skip）
#       -> app/services/media_service.py（上傳驗證與重新編碼）
#
# 檔案路徑：tests/test_storage_backends.py
# 建立日期：2026-08-14 / 版本：v1.0
#
# 模組定位（SAI §20 Security 層 + §10.4 Storage Adapter）：
#   「Malicious upload -> MIME/extension allowlist、重新命名、
#     尺寸上限、圖片重新編碼；非允許類型拒絕」
#
# 對應驗收條目：
#   AC-11 上傳非法副檔名被拒絕
#   AC-12 有圖無 alt 的相關前置（alt 檢查在 test_people/test_research）
#   SAI §16 Filename：伺服器生成名稱，不使用 user filename
#   SAI §11.2 Malicious upload：偽裝檔案必須被擋下
#
# 特殊機制：
#   同一組行為測試同時套用在 LocalStorage 與 GcsStorage，
#   確保兩者行為一致（SAI §10.4 的核心要求）。
#   沒有 GCS 憑證時，GCS 部分自動 skip 而非失敗。
#
# 驗證方式：
#   pytest tests/test_storage_backends.py -v
# ============================================================

from __future__ import annotations

import io

import pytest


# ----------------------------------------------------------------------
# object key 驗證（所有後端共用）
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "bad_key",
    [
        "",
        "../etc/passwd",
        "people/../../secret.txt",
        "people/UPPERCASE.JPG",
        "people/with space.jpg",
        "people/中文檔名.jpg",
        "..",
    ],
)
def test_invalid_object_keys_are_rejected(bad_key):
    """不合法或危險的 object key 一律拒絕。

    這是防止任意檔案寫入的第一道防線，
    且同時套用於 local 與 GCS（規則定義在 base.py）。
    """
    from app.storage.base import StorageError, validate_object_key

    with pytest.raises(StorageError):
        validate_object_key(bad_key)


@pytest.mark.parametrize(
    "good_key",
    [
        "people/abc123.jpg",
        "research/0f9a-2b.webp",
        "site/logo.png",
        "people/a.b.c.jpg",
    ],
)
def test_valid_object_keys_are_accepted(good_key):
    """合法 key 通過驗證。"""
    from app.storage.base import validate_object_key

    assert validate_object_key(good_key) == good_key


def test_leading_slash_is_normalised_not_rejected():
    """前導斜線被正規化為相對路徑，而不是拒絕。

    為什麼這樣是安全的：
      去掉前導斜線後，key 仍然被限制在 uploads 根目錄之下，
      無法指向檔案系統的絕對位置。".." 的跳脫則另外被明確拒絕。
      把 "/etc/passwd" 正規化為 "etc/passwd" 只是相對路徑，
      不構成越權存取。

    為什麼不乾脆拒絕：
      正規化讓 validate_object_key 具備冪等性 ——
      對同一個 key 重複呼叫會得到相同結果，
      這對 media manifest 的比對與備份還原很重要。
    """
    from app.storage.base import validate_object_key

    assert validate_object_key("/people/a.jpg") == "people/a.jpg"
    assert validate_object_key("//people//a.jpg") == "people/a.jpg"


def test_path_traversal_rejected_by_local_backend(app, png_bytes):
    """LocalStorage 的第二層防護：解析後不得逃出 uploads 根目錄。"""
    from app.storage.base import StorageError
    from app.storage.local import LocalStorage

    with app.app_context():
        backend = LocalStorage(app.config["UPLOAD_DIR"])

        with pytest.raises(StorageError):
            backend.save(io.BytesIO(png_bytes), "../escaped.png", "image/png")


# ----------------------------------------------------------------------
# LocalStorage 行為契約
# ----------------------------------------------------------------------
def test_local_backend_roundtrip(app, png_bytes):
    """save -> exists -> open -> delete 的完整流程。"""
    from app.storage.local import LocalStorage

    with app.app_context():
        backend = LocalStorage(app.config["UPLOAD_DIR"])

        stored = backend.save(io.BytesIO(png_bytes), "people/test-key.png", "image/png")

        assert stored.key == "people/test-key.png"
        assert stored.size == len(png_bytes)
        assert len(stored.checksum_sha256) == 64
        assert backend.exists("people/test-key.png")

        with backend.open("people/test-key.png") as stream:
            assert stream.read() == png_bytes

        assert backend.delete("people/test-key.png") is True
        assert backend.exists("people/test-key.png") is False


def test_local_delete_missing_key_returns_false(app):
    """刪除不存在的物件回 False，不拋錯（介面契約）。"""
    from app.storage.local import LocalStorage

    with app.app_context():
        backend = LocalStorage(app.config["UPLOAD_DIR"])
        assert backend.delete("people/never-existed.png") is False


def test_local_public_url_does_not_touch_filesystem(app):
    """public_url 不查詢後端（效能契約，見 base.py）。"""
    from app.storage.local import LocalStorage

    with app.app_context():
        backend = LocalStorage(app.config["UPLOAD_DIR"])
        # 即使檔案不存在也要能產生 URL。
        assert backend.public_url("people/missing.png") == "/uploads/people/missing.png"


def test_local_save_is_atomic_on_overwrite(app, png_bytes):
    """重複寫入同一 key 以最後一次為準，且不留下 .tmp 檔。"""
    from pathlib import Path

    from app.storage.local import LocalStorage

    with app.app_context():
        backend = LocalStorage(app.config["UPLOAD_DIR"])
        backend.save(io.BytesIO(b"first-version-bytes"), "people/x.png", "image/png")
        backend.save(io.BytesIO(png_bytes), "people/x.png", "image/png")

        with backend.open("people/x.png") as stream:
            assert stream.read() == png_bytes

        leftovers = list(Path(app.config["UPLOAD_DIR"]).rglob("*.tmp"))
        assert not leftovers, f"不得留下暫存檔：{leftovers}"


def test_local_health_check_reports_writable(app):
    """health_check 回報可寫狀態（Admin System health）。"""
    from app.storage.local import LocalStorage

    with app.app_context():
        ok, detail = LocalStorage(app.config["UPLOAD_DIR"]).health_check()
        assert ok is True
        assert detail


# ----------------------------------------------------------------------
# Backend 工廠
# ----------------------------------------------------------------------
def test_unknown_backend_raises_instead_of_falling_back(app):
    """未知的 STORAGE_BACKEND 必須拋錯，不得靜默退回 local。

    靜默退回會讓 production 把檔案寫進 Cloud Run 的
    暫存檔案系統並在重啟後消失 [S17] —— 無聲的資料遺失
    比啟動失敗嚴重得多。
    """
    from app.storage import init_storage
    from app.storage.base import StorageError

    app.config["STORAGE_BACKEND"] = "dropbox"
    with pytest.raises(StorageError):
        init_storage(app)


def test_gcs_backend_requires_bucket_name():
    """未提供 GCS_BUCKET 時必須明確報錯。"""
    from app.storage.base import StorageError
    from app.storage.gcs import GcsStorage

    with pytest.raises(StorageError):
        GcsStorage(bucket_name=None)


def test_gcs_public_url_format():
    """GCS public_url 的格式（不需要憑證即可驗證）。"""
    from app.storage.gcs import GcsStorage

    backend = GcsStorage(bucket_name="siph-media")
    assert (
        backend.public_url("people/a.jpg")
        == "https://storage.googleapis.com/siph-media/people/a.jpg"
    )

    cdn = GcsStorage(bucket_name="siph-media", public_base_url="https://cdn.example.edu/")
    assert cdn.public_url("people/a.jpg") == "https://cdn.example.edu/people/a.jpg"


def test_gcs_backend_validates_keys_like_local():
    """GCS 後端套用與 local 完全相同的 key 規則（SAI §10.4）。"""
    from app.storage.base import StorageError
    from app.storage.gcs import GcsStorage

    backend = GcsStorage(bucket_name="siph-media")
    with pytest.raises(StorageError):
        backend.public_url("../escape.jpg")


# ----------------------------------------------------------------------
# AC-11：上傳安全
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "filename",
    ["evil.php", "script.js", "shell.sh", "doc.pdf", "archive.zip", "vector.svg", "noext"],
)
def test_ac11_disallowed_extensions_are_rejected(app, png_bytes, filename):
    """AC-11：非允許副檔名被拒絕（含 SVG，SAI §16）。"""
    from werkzeug.datastructures import FileStorage

    from app.services.media_service import MediaError, MediaService

    with app.app_context():
        upload = FileStorage(
            stream=io.BytesIO(png_bytes), filename=filename, content_type="image/png"
        )
        with pytest.raises(MediaError):
            MediaService.save_image(upload, purpose="people")


def test_ac11_disguised_file_is_rejected(app):
    """副檔名偽裝成圖片但內容不是圖片 -> 拒絕。

    這是 SAI §11.2「上傳偽裝程式檔」的核心測試：
    只檢查副檔名不夠，必須真的能被解碼成影像。
    """
    from werkzeug.datastructures import FileStorage

    from app.services.media_service import MediaError, MediaService

    with app.app_context():
        upload = FileStorage(
            stream=io.BytesIO(b"<?php system($_GET['c']); ?>"),
            filename="innocent.jpg",
            content_type="image/jpeg",
        )
        with pytest.raises(MediaError):
            MediaService.save_image(upload, purpose="people")


def test_ac11_oversized_upload_is_rejected(app, png_bytes):
    """超過大小上限的檔案被拒絕（SAI §16）。"""
    from werkzeug.datastructures import FileStorage

    from app.services.media_service import MediaError, MediaService

    with app.app_context():
        # 上限必須明顯小於測試用圖片（12x12 PNG 約數十 bytes），
        # 否則測試會因為圖片剛好小於上限而假通過。
        app.config["MAX_CONTENT_LENGTH"] = 10
        assert len(png_bytes) > 10, "前置條件：測試圖片必須大於上限"

        upload = FileStorage(
            stream=io.BytesIO(png_bytes), filename="big.png", content_type="image/png"
        )
        with pytest.raises(MediaError):
            MediaService.save_image(upload, purpose="people")


def test_empty_upload_is_rejected(app):
    """空檔案被拒絕。"""
    from werkzeug.datastructures import FileStorage

    from app.services.media_service import MediaError, MediaService

    with app.app_context():
        upload = FileStorage(
            stream=io.BytesIO(b""), filename="empty.png", content_type="image/png"
        )
        with pytest.raises(MediaError):
            MediaService.save_image(upload, purpose="people")


def test_uploaded_filename_is_not_used_as_object_key(app, png_bytes):
    """SAI §16：伺服器生成檔名，不使用使用者提供的檔名。"""
    from werkzeug.datastructures import FileStorage

    from app.services.media_service import MediaService

    with app.app_context():
        upload = FileStorage(
            stream=io.BytesIO(png_bytes),
            filename="my-personal-photo.png",
            content_type="image/png",
        )
        saved = MediaService.save_image(upload, purpose="people")

        assert "my-personal-photo" not in saved.key
        assert saved.key.startswith("people/")
        assert saved.key.endswith(".png")


def test_upload_is_re_encoded_and_strips_metadata(app):
    """SAI §11.2/§16：圖片重新編碼並移除 EXIF（含 GPS）。

    做法：建立一張帶 EXIF 的 JPEG，上傳後讀回確認 EXIF 已消失。
    這同時驗證了「重新編碼」確實發生 —— 若只是原樣存檔，
    EXIF 會被保留。
    """
    from PIL import Image
    from werkzeug.datastructures import FileStorage

    from app.services.media_service import MediaService
    from app.storage import get_storage

    with app.app_context():
        # 建立帶 EXIF 的來源影像。
        source = io.BytesIO()
        image = Image.new("RGB", (40, 40), color=(200, 100, 50))
        exif = Image.Exif()
        exif[271] = "TestCameraMake"  # Make
        exif[272] = "SecretModel"     # Model
        image.save(source, format="JPEG", exif=exif)
        source.seek(0)

        original_exif = Image.open(io.BytesIO(source.getvalue())).getexif()
        assert original_exif, "前置條件：來源影像應含 EXIF"

        source.seek(0)
        saved = MediaService.save_image(
            FileStorage(stream=source, filename="photo.jpg", content_type="image/jpeg"),
            purpose="people",
        )

        with get_storage().open(saved.key) as stream:
            stored_exif = Image.open(io.BytesIO(stream.read())).getexif()

        assert not dict(stored_exif), (
            "上傳後的影像不得保留 EXIF（隱私與 SAI §16「移除不必要 metadata」）"
        )


def test_large_image_is_downscaled(app):
    """超過用途上限的影像會被等比例縮小（SAI §16 Processing）。"""
    from PIL import Image
    from werkzeug.datastructures import FileStorage

    from app.services.media_service import MediaService

    with app.app_context():
        source = io.BytesIO()
        Image.new("RGB", (3000, 3000), color=(10, 20, 30)).save(source, format="PNG")
        source.seek(0)

        saved = MediaService.save_image(
            FileStorage(stream=source, filename="huge.png", content_type="image/png"),
            purpose="people",
        )

        # people 用途上限為 800x800。
        assert saved.width <= 800 and saved.height <= 800


def test_small_image_is_not_upscaled(app, png_bytes):
    """小於上限的影像不會被放大（避免品質損失）。"""
    from werkzeug.datastructures import FileStorage

    from app.services.media_service import MediaService

    with app.app_context():
        saved = MediaService.save_image(
            FileStorage(
                stream=io.BytesIO(png_bytes), filename="small.png", content_type="image/png"
            ),
            purpose="people",
        )
        assert saved.width == 12 and saved.height == 12


def test_checksum_of_stored_object(app, png_bytes):
    """checksum_of 可讀回並計算 SHA-256（AC-24 驗證用）。"""
    from werkzeug.datastructures import FileStorage

    from app.services.media_service import MediaService

    with app.app_context():
        saved = MediaService.save_image(
            FileStorage(
                stream=io.BytesIO(png_bytes), filename="x.png", content_type="image/png"
            ),
            purpose="research",
        )
        assert MediaService.checksum_of(saved.key) == saved.checksum_sha256
        assert MediaService.checksum_of("people/does-not-exist.png") is None


def test_unknown_purpose_is_rejected(app, png_bytes):
    """未知的上傳用途被拒絕（防止任意路徑前綴）。"""
    from werkzeug.datastructures import FileStorage

    from app.services.media_service import MediaError, MediaService

    with app.app_context():
        with pytest.raises(MediaError):
            MediaService.save_image(
                FileStorage(
                    stream=io.BytesIO(png_bytes), filename="x.png", content_type="image/png"
                ),
                purpose="../../etc",
            )


def test_uploads_route_disabled_for_non_local_backend(app, client):
    """STORAGE_BACKEND 非 local 時，/uploads/ 路由回 404。

    正式環境由 GCS 直接服務媒體，應用程式不應代理檔案
    （SAI §21.4 成本與延遲考量）。
    """
    app.config["STORAGE_BACKEND"] = "gcs"
    assert client.get("/uploads/people/anything.jpg").status_code == 404
