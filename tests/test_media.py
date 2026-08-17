# ============================================================
# NTUST SiPh Lab - Media Service Tests
#
# 檔案路徑：tests/test_media.py
# 建立日期：2026-08-15 / 版本：v1.0
#
# 模組定位（SAI §16 Media / Upload、§20 Security 層）：
#   上傳是本系統唯一接受「任意二進位輸入」的入口，
#   因此是攻擊面最大的地方。SAI §16 要求：
#     - 副檔名 allowlist
#     - 伺服器生成檔名（不使用 user filename）
#     - 尺寸上限與重新編碼
#     - 移除不必要 metadata
#
#   本檔驗證這些控制真的生效，而不只是宣稱存在。
#
# 為什麼要測「重新編碼」：
#   單純檢查副檔名或 MIME 完全擋不住 polyglot 檔案
#   （同時是合法圖片與可執行內容）。真正的保證來自
#   「用 Pillow 解碼後重新輸出」—— 輸出的位元組由 Pillow 產生，
#   原始檔案中夾帶的任何內容都不會留存。
#
# 驗證方式：
#   pytest tests/test_media.py -v
# ============================================================

from __future__ import annotations

import io

import pytest
from werkzeug.datastructures import FileStorage

from app.services.media_service import MediaError, MediaService


def _image_bytes(fmt="JPEG", size=(800, 600), colour=(10, 35, 61)) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    mode = "RGB" if fmt in ("JPEG", "WEBP") else "RGBA"
    Image.new(mode, size, colour if mode == "RGB" else (*colour, 255)).save(buffer, format=fmt)
    return buffer.getvalue()


def _upload(data: bytes, filename: str, content_type="image/jpeg") -> FileStorage:
    return FileStorage(stream=io.BytesIO(data), filename=filename, content_type=content_type)


# ----------------------------------------------------------------------
# 副檔名 allowlist
# ----------------------------------------------------------------------
@pytest.mark.parametrize("name", ["a.jpg", "a.jpeg", "a.png", "a.webp", "A.JPG", "a.PnG"])
def test_allowed_filenames(app, name):
    with app.app_context():
        assert MediaService.is_allowed_filename(name) is True


@pytest.mark.parametrize("name", [
    None, "", "noextension",
    "a.php", "a.svg", "a.gif", "a.bmp", "a.exe", "a.sh", "a.html",
    "a.jpg.php",          # 雙副檔名，實際是 .php
])
def test_rejected_filenames(app, name):
    with app.app_context():
        assert MediaService.is_allowed_filename(name) is False


# ----------------------------------------------------------------------
# 實際儲存
# ----------------------------------------------------------------------
def test_save_image_returns_metadata(app):
    with app.app_context():
        saved = MediaService.save_image(_upload(_image_bytes(), "portrait.jpg"), "people")

    assert saved.key.startswith("people/")
    assert saved.key.endswith(".jpg")
    assert saved.size > 0
    assert len(saved.checksum_sha256) == 64
    assert saved.width > 0 and saved.height > 0


def test_save_image_ignores_user_filename(app):
    """SAI §16：伺服器生成檔名，不使用 user filename 作實際路徑。

    這同時擋掉路徑穿越（../../etc/passwd）與檔名注入。
    """
    with app.app_context():
        saved = MediaService.save_image(
            _upload(_image_bytes(), "../../../etc/passwd.jpg"), "people"
        )

    assert ".." not in saved.key
    assert "passwd" not in saved.key
    assert saved.key.startswith("people/")


def test_save_image_generates_unique_keys(app):
    """相同檔名多次上傳不得互相覆蓋。"""
    with app.app_context():
        first = MediaService.save_image(_upload(_image_bytes(), "photo.jpg"), "people")
        second = MediaService.save_image(_upload(_image_bytes(), "photo.jpg"), "people")
    assert first.key != second.key


@pytest.mark.parametrize("purpose", ["people", "research", "site"])
def test_purpose_controls_key_prefix(app, purpose):
    with app.app_context():
        saved = MediaService.save_image(_upload(_image_bytes(), "x.jpg"), purpose)
    assert saved.key.startswith(f"{purpose}/")


def test_unknown_purpose_rejected(app):
    with app.app_context():
        with pytest.raises(MediaError, match="用途"):
            MediaService.save_image(_upload(_image_bytes(), "x.jpg"), "../evil")


# ----------------------------------------------------------------------
# 惡意輸入
# ----------------------------------------------------------------------
def test_disguised_php_rejected(app):
    """副檔名偽裝 + 宣告 image/jpeg，內容其實是 PHP。"""
    payload = b"<?php system($_GET['c']); ?>"
    with app.app_context():
        with pytest.raises(MediaError):
            MediaService.save_image(_upload(payload, "evil.php"), "people")


def test_php_content_with_image_extension_rejected(app):
    """副檔名合法但內容不是影像 —— 必須被 Pillow 解碼檢查擋下。

    這是最重要的一條：只看副檔名完全擋不住這種攻擊。
    """
    payload = b"<?php system($_GET['c']); ?>"
    with app.app_context():
        with pytest.raises(MediaError, match="影像"):
            MediaService.save_image(_upload(payload, "evil.jpg"), "people")


def test_svg_with_script_rejected(app):
    payload = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    with app.app_context():
        with pytest.raises(MediaError):
            MediaService.save_image(_upload(payload, "x.svg", "image/svg+xml"), "people")


def test_empty_file_rejected(app):
    with app.app_context():
        with pytest.raises(MediaError, match="空"):
            MediaService.save_image(_upload(b"", "empty.jpg"), "people")


def test_no_file_rejected(app):
    with app.app_context():
        with pytest.raises(MediaError):
            MediaService.save_image(None, "people")


def test_oversized_file_rejected(app):
    """超過 MAX_CONTENT_LENGTH 的第二道防線（SAI §16）。"""
    with app.app_context():
        app.config["MAX_CONTENT_LENGTH"] = 1024      # 1 KB
        with pytest.raises(MediaError, match="過大"):
            MediaService.save_image(
                _upload(_image_bytes(size=(2000, 2000)), "big.jpg"), "people"
            )


# ----------------------------------------------------------------------
# 重新編碼與 metadata 移除
# ----------------------------------------------------------------------
def test_image_is_reencoded_not_stored_verbatim(app):
    """輸出必須是 Pillow 重新產生的位元組，而非原始檔案。

    這是防 polyglot 檔案的關鍵：即使原始檔案在合法影像資料後
    附加了可執行內容，重新編碼後那些位元組不會留存。
    """
    original = _image_bytes()
    # 在合法 JPEG 之後附加額外資料（polyglot 手法）
    polyglot = original + b"<?php system($_GET['c']); ?>"

    with app.app_context():
        saved = MediaService.save_image(_upload(polyglot, "polyglot.jpg"), "people")

        from app.storage import get_storage

        stored = get_storage().open(saved.key).read()

    assert b"<?php" not in stored, "重新編碼後不得保留附加的可執行內容"
    assert stored != polyglot


def test_large_image_is_downscaled(app):
    """超過尺寸上限的圖片必須縮放（SAI §16 Processing）。"""
    with app.app_context():
        saved = MediaService.save_image(
            _upload(_image_bytes(size=(4000, 3000)), "huge.jpg"), "people"
        )
    assert saved.width < 4000, "應縮放至合理最大寬"


def test_small_image_not_upscaled(app):
    with app.app_context():
        saved = MediaService.save_image(
            _upload(_image_bytes(size=(120, 90)), "small.jpg"), "people"
        )
    assert (saved.width, saved.height) == (120, 90)


def test_png_stays_png(app):
    with app.app_context():
        saved = MediaService.save_image(
            _upload(_image_bytes(fmt="PNG"), "x.png", "image/png"), "people"
        )
    assert saved.key.endswith(".png")
    assert saved.content_type == "image/png"


# ----------------------------------------------------------------------
# public_url
# ----------------------------------------------------------------------
def test_public_url_none_for_missing_key(app):
    with app.app_context():
        assert MediaService.public_url(None) is None
        assert MediaService.public_url("") is None


def test_public_url_for_stored_object(app):
    with app.app_context():
        saved = MediaService.save_image(_upload(_image_bytes(), "x.jpg"), "people")
        url = MediaService.public_url(saved.key)
    assert url
    assert saved.key in url


# ----------------------------------------------------------------------
# 解壓炸彈（交付前審查 REV-107）
# ----------------------------------------------------------------------
# MAX_CONTENT_LENGTH 限制的是「壓縮後」大小，不是解碼後的記憶體。
# 實測：12000x12000 純色 PNG 編碼後僅約 435 KB（遠低於 8 MB 上限），
# 解碼卻需要約 432 MB —— 而 Pillow 在 89~179 MPx 之間只發
# DecompressionBombWarning 不會拋錯。Cloud Run 預設記憶體 512 MiB，
# 單一請求即可觸發 OOM 並重啟 instance。


def test_decompression_bomb_rejected_before_decode(app):
    """遠低於大小上限、但解碼後極大的影像必須被拒絕。"""
    data = _image_bytes("PNG", size=(12000, 12000))

    # 前提確認：這個檔案確實通過了大小檢查，
    # 所以擋下它的必定是像素數檢查，而不是 MAX_CONTENT_LENGTH。
    assert len(data) < 8 * 1024 * 1024

    with app.app_context():
        with pytest.raises(MediaError) as exc:
            MediaService.save_image(_upload(data, "bomb.png", "image/png"), "people")

    assert "百萬像素" in str(exc.value)
