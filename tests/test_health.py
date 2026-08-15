# ============================================================
# NTUST SiPh Lab - Health Service Tests
#
# 檔案路徑：tests/test_health.py
# 建立日期：2026-08-15 / 版本：v1.0
#
# 模組定位（SAI §19、§7.2 System health、附錄 A /healthz）：
#   /healthz 是 Cloud Run 判斷 instance 是否存活的依據，
#   也是 §21.4 每次部署後必跑的第一項檢查。
#   它必須「輕量、不回傳敏感資料、DB 故障時能正確回報」。
#
# 驗證方式：
#   pytest tests/test_health.py -v
# ============================================================

from __future__ import annotations

from app.services.health_service import HealthService


# ----------------------------------------------------------------------
# liveness
# ----------------------------------------------------------------------
def test_liveness_ok_when_db_reachable(app):
    with app.app_context():
        ok, message = HealthService.liveness()
    assert ok is True
    assert message


def test_liveness_does_not_leak_connection_details(app):
    """健康檢查不得洩漏連線字串、密碼或檔案路徑。"""
    with app.app_context():
        _, message = HealthService.liveness()

    lowered = message.lower()
    for leak in ("password", "sqlite:///", "postgresql://", "secret", "@"):
        assert leak not in lowered, f"健康檢查訊息不得包含 {leak!r}：{message}"


def test_liveness_reports_failure_when_db_broken(app, monkeypatch):
    """DB 不可用時必須回報失敗，而不是拋例外讓 /healthz 變成 500。

    Cloud Run 需要一個「明確回答不健康」的回應才能正確重啟 instance。
    """
    from app.extensions import db

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated database outage")

    with app.app_context():
        monkeypatch.setattr(db.session, "execute", _boom)
        ok, message = HealthService.liveness()

    assert ok is False
    assert message


# ----------------------------------------------------------------------
# /healthz 端點
# ----------------------------------------------------------------------
def test_healthz_returns_200(client):
    response = client.get("/healthz")
    assert response.status_code == 200


def test_healthz_body_is_minimal(client):
    """SAI §19：不回傳敏感資料。"""
    body = client.get("/healthz").get_data(as_text=True)
    assert len(body) < 200
    for leak in ("Traceback", "sqlite", "postgresql", "SECRET"):
        assert leak not in body


def test_healthz_is_not_in_sitemap(client):
    """健康檢查端點不應被索引。"""
    assert "/healthz" not in client.get("/sitemap.xml").get_data(as_text=True)


# ----------------------------------------------------------------------
# backup 狀態（SAI §7.2 System health）
# ----------------------------------------------------------------------
def test_last_backup_none_when_no_backups(app):
    with app.app_context():
        assert HealthService.last_backup() is None


def test_last_backup_reports_existing_archive(app):
    """建立備份後，System health 應能回報最後一次成功時間。"""
    from pathlib import Path

    import scripts.backup_sqlite as backup

    with app.app_context():
        archive = backup.create_backup(
            database_url=app.config["SQLALCHEMY_DATABASE_URI"],
            upload_dir=Path(app.config["UPLOAD_DIR"]),
            backup_dir=Path(app.config["BACKUP_DIR"]),
        )
        assert archive.exists()

        info = HealthService.last_backup()

    assert info is not None, "已有備份時 System health 必須回報"
    assert any(info.get(k) for k in ("name", "filename", "path", "created_at"))


# ----------------------------------------------------------------------
# system_report（Admin System 頁）
# ----------------------------------------------------------------------
def test_system_report_returns_checks(app):
    with app.app_context():
        report = HealthService.system_report()

    assert isinstance(report, list)
    assert report, "System health 應至少回報一項檢查"
    for item in report:
        assert "label" in item or "name" in item


def test_system_report_covers_db_and_storage(app):
    """SAI §7.2：System health 需涵蓋 DB writable、upload writable、backup。"""
    with app.app_context():
        report = HealthService.system_report()

    blob = " ".join(str(v) for item in report for v in item.values()).lower()
    assert "db" in blob or "資料庫" in blob
    assert "upload" in blob or "上傳" in blob or "媒體" in blob
