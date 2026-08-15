# ============================================================
# NTUST SiPh Lab - Site Settings Tests
#
# 檔案路徑：tests/test_settings.py
# 建立日期：2026-08-15 / 版本：v1.0
#
# 模組定位（SAI §8.6、§15.4）：
#   SiteSetting 是 singleton（id=1），承載站名、Hero、聯絡、
#   招募、SEO 預設值與外部識別。它同時是 LC-001、LC-006~013
#   的落點，因此行為錯誤會直接造成 legacy 遷移驗證失敗。
#
# 重點：
#   - singleton 語意：永遠只有一筆，get() 必要時建立
#   - 更新後前台立即反映
#   - 媒體（logo / hero / og image）的設定與移除
#   - research_focus 的結構化存取
#
# 驗證方式：
#   pytest tests/test_settings.py -v
# ============================================================

from __future__ import annotations

import io

import pytest
from PIL import Image
from werkzeug.datastructures import FileStorage

from app.models.site_setting import SiteSetting
from app.services.settings_service import SettingsService, SettingsServiceError


def _image() -> FileStorage:
    buffer = io.BytesIO()
    Image.new("RGB", (300, 300), (16, 35, 61)).save(buffer, format="PNG")
    buffer.seek(0)
    return FileStorage(stream=buffer, filename="logo.png", content_type="image/png")


# ----------------------------------------------------------------------
# Singleton 語意
# ----------------------------------------------------------------------
def test_get_creates_singleton_when_absent(app):
    with app.app_context():
        settings = SiteSetting.get()
        assert settings is not None
        assert settings.id == 1


def test_get_is_idempotent(app):
    """重複呼叫不得產生第二筆（SAI §8.6 singleton row）。"""
    from app.extensions import db

    with app.app_context():
        SiteSetting.get()
        SiteSetting.get()
        SiteSetting.get()
        assert db.session.query(SiteSetting).count() == 1


# ----------------------------------------------------------------------
# 更新
# ----------------------------------------------------------------------
def test_update_persists_identity_fields(app):
    with app.app_context():
        SettingsService.update({
            "lab_name_zh": "矽光子研究室",
            "lab_name_en": "Silicon Photonics Lab",
            "university_zh": "國立臺灣科技大學",
            "department_zh": "電子工程系",
        })
        settings = SiteSetting.get()

    assert settings.lab_name_zh == "矽光子研究室"
    assert settings.lab_name_en == "Silicon Photonics Lab"


def test_update_reflects_on_public_pages(app, client):
    """設定變更必須立即反映在前台（LC-001 的驗證依據）。"""
    with app.app_context():
        SettingsService.update({"lab_name_zh": "遷移驗證用實驗室名稱"})

    assert "遷移驗證用實驗室名稱" in client.get("/").get_data(as_text=True)


def test_update_normalises_email(app):
    with app.app_context():
        SettingsService.update({"contact_email": "  YangCL@Mail.NTUST.edu.TW "})
        assert SiteSetting.get().contact_email == "yangcl@mail.ntust.edu.tw"


def test_update_drops_invalid_email(app):
    """非法 email 不得被存入。

    service 層採「正規化為 None」而非拋錯 —— 使用者可見的錯誤訊息
    由表單層負責（見 tests/test_admin_forms.py）。這裡驗證的是
    「即使繞過表單，髒資料也進不了資料庫」這道防線。
    """
    with app.app_context():
        SettingsService.update({"contact_email": "not-an-email"})
        assert SiteSetting.get().contact_email is None


def test_update_drops_dangerous_url(app):
    """javascript: 這類 scheme 絕不能寫進資料庫。"""
    with app.app_context():
        SettingsService.update({"map_url": "javascript:alert(1)"})
        assert SiteSetting.get().map_url is None


def test_update_ignores_unknown_fields(app):
    """未知欄位不得寫入（避免透過表單注入任意屬性）。"""
    with app.app_context():
        SettingsService.update({"lab_name_zh": "測試", "is_superuser": True})
        settings = SiteSetting.get()
        assert not hasattr(settings, "is_superuser") or settings.is_superuser is not True


def test_blank_values_become_none(app):
    with app.app_context():
        SettingsService.update({"address_zh": "   "})
        assert SiteSetting.get().address_zh is None


# ----------------------------------------------------------------------
# 研究方向（LC-006 ~ LC-011）
# ----------------------------------------------------------------------
def test_research_focus_roundtrip(app):
    """六項研究專長必須完整保存且順序穩定（AC-23 set comparison）。"""
    titles = ["光電感測技術", "矽光子技術", "光通道效能監視",
              "光通訊系統", "物聯網平台", "人工智慧技術應用"]
    items = [{"title_zh": t} for t in titles]

    with app.app_context():
        SettingsService.update({"research_focus": items})
        stored = [i["title_zh"] for i in SiteSetting.get().research_focus]

    assert stored == titles, "六項專長必須完整且順序一致（AC-23 set comparison）"


def test_research_focus_drops_untitled_entries(app):
    """沒有標題的項目無意義，應被略過而非存入空白項。"""
    with app.app_context():
        SettingsService.update({"research_focus": [
            {"title_zh": "矽光子技術"},
            {"title_zh": "   "},
            {"description_zh": "只有描述沒有標題"},
        ]})
        assert len(SiteSetting.get().research_focus) == 1


def test_research_focus_does_not_invent_descriptions(app):
    """SAI §2.3：母站只提供專長名稱，不得由系統補寫定義。"""
    with app.app_context():
        SettingsService.update({"research_focus": [{"title_zh": "矽光子技術"}]})
        item = SiteSetting.get().research_focus[0]

    assert item["description_zh"] == "", "不得自動產生描述文字"


def test_research_focus_appears_on_public_pages(app, client):
    titles = ["光電感測技術", "矽光子技術"]
    with app.app_context():
        SettingsService.update({"research_focus": [{"title_zh": t} for t in titles]})

    home = client.get("/").get_data(as_text=True)
    for title in titles:
        assert title in home


# ----------------------------------------------------------------------
# 媒體
# ----------------------------------------------------------------------
def test_update_media_sets_logo(app):
    with app.app_context():
        SettingsService.update_media("logo_path", _image())
        assert SiteSetting.get().logo_path


def test_logo_renders_in_header(app, client):
    """上傳校徽後應出現在頁首（使用者需求）。"""
    with app.app_context():
        SettingsService.update_media("logo_path", _image())
        key = SiteSetting.get().logo_path

    home = client.get("/").get_data(as_text=True)
    assert key in home, "校徽上傳後應顯示於頁首"


def test_remove_media_clears_field_and_file(app):
    with app.app_context():
        SettingsService.update_media("logo_path", _image())
        key = SiteSetting.get().logo_path

        SettingsService.remove_media("logo_path")
        assert SiteSetting.get().logo_path is None

        from app.storage import get_storage

        assert not get_storage().exists(key), "移除設定時應一併刪除實體檔案"


def test_update_media_rejects_unknown_field(app):
    """欄位名稱來自 URL，必須以 allowlist 限制。

    否則 /admin/settings/media/<field>/delete 會變成任意屬性寫入。
    """
    with app.app_context():
        with pytest.raises(SettingsServiceError):
            SettingsService.update_media("password_hash", _image())


def test_remove_media_rejects_unknown_field(app):
    with app.app_context():
        with pytest.raises(SettingsServiceError):
            SettingsService.remove_media("../../etc/passwd")


def test_update_media_rejects_non_image(app):
    with app.app_context():
        bad = FileStorage(
            stream=io.BytesIO(b"<?php system($_GET['c']); ?>"),
            filename="evil.php",
            content_type="image/png",
        )
        with pytest.raises((SettingsServiceError, Exception)):
            SettingsService.update_media("logo_path", bad)
