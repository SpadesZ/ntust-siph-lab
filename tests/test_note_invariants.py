# ============================================================
# NTUST SiPh Lab - NOTE 碼層決策的行為斷言
#
# 上下游：
#   docs/NOTES.md 登記決策 -> 程式中以 NOTE(NOTE-NNN) 標記
#       -> 本檔對「沒有其他測試保護」的那幾則寫出可執行斷言
#       -> tests/test_repo_integrity.py 檢查編號雙向閉環
#
# 檔案路徑：tests/test_note_invariants.py
# 建立日期：2026-08-18 / 版本：v1.0
#
# 功能說明：
#   替 docs/NOTES.md 裡幾則「原本沒有任何測試保護」的決策補上斷言，
#   讓它們不能被無聲改掉。目前涵蓋 NOTE-005（公開頁不得寫入）、
#   NOTE-007（稽核 IP 不得有預設 salt）與 NOTE-008（稽核截斷不拋錯）。
#
# 模組定位：
#   NOTE 制度的「驗證層」。只放那些**跨模組、屬於決策層級**、
#   且在既有測試檔中沒有自然歸屬的不變量。
#   不是 NOTE 的總測試清單——多數 NOTE 已由對應領域的測試涵蓋
#   （例如 NOTE-001 在 test_auth.py、NOTE-002 在
#   test_auth.py::test_ac02_unknown_user_and_wrong_password_are_indistinguishable）。
#
# 主要責任：
#   1. NOTE-005：公開頁的 GET 不得產生 INSERT/UPDATE/DELETE。
#   2. NOTE-007：未提供高熵 salt 時不得記錄 IP，且不得有硬編碼預設 salt。
#   3. NOTE-008：稽核 summary 過長時截斷而非拋錯。
#
# 維護契約：
#   - 新增 NOTE 時先問「既有測試檔有沒有自然歸屬」；有就寫在那邊，
#     並在 NOTES.md 的「驗證」欄指過去。本檔只收沒有歸屬的。
#   - 本檔的測試若失敗，代表**決策被改動**，不是測試壞掉。
#     先回 docs/NOTES.md 確認該決策是否已正式撤銷，再決定改碼還是改測試。
#
# 驗證方式：
#   pytest tests/test_note_invariants.py -v
# ============================================================

from __future__ import annotations

import os
from contextlib import contextmanager

import pytest
from sqlalchemy import event

from app.extensions import db
from app.models import audit_log as audit_log_module
from app.models.audit_log import AuditLog

#: 不需要 fixture 資料就能開啟的公開頁。
_PUBLIC_PAGES = ["/", "/about", "/members", "/alumni", "/research", "/join",
                 "/sitemap.xml", "/robots.txt"]


# ----------------------------------------------------------------------
# NOTE-005：公開頁的任何 GET 都不得產生資料庫寫入
# ----------------------------------------------------------------------
@contextmanager
def _capture_write_statements(engine):
    """攔截並記錄所有 INSERT / UPDATE / DELETE 敘述。

    為什麼不比對資料列數：
      數量比對會漏掉「寫入後又改回原值」與「寫入後 rollback」——
      兩者都仍然對資料庫產生了寫入壓力，也都違反本決策。
      直接看實際送出的 SQL 才是精確的判準。
    """
    captured: list[str] = []

    def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        head = statement.lstrip().split(None, 1)
        if head and head[0].upper() in {"INSERT", "UPDATE", "DELETE"}:
            captured.append(" ".join(statement.split())[:120])

    event.listen(engine, "before_cursor_execute", _before_cursor_execute)
    try:
        yield captured
    finally:
        event.remove(engine, "before_cursor_execute", _before_cursor_execute)


def test_note005_public_get_requests_do_not_write_to_database(app, client, sample_person):
    """公開頁的 GET 一律唯讀（NOTE-005）。

    NOTE-005 明列唯一例外是 SiteSetting.get() 首次建立預設值，
    因此先暖機一輪讓預設值就位，再斷言穩定狀態下不再有任何寫入。
    """
    for path in _PUBLIC_PAGES:
        client.get(path)
    client.get(f"/people/{sample_person['slug']}")

    with app.app_context():
        engine = db.engine

    with _capture_write_statements(engine) as writes:
        for path in _PUBLIC_PAGES:
            response = client.get(path)
            assert response.status_code == 200, f"{path} 回應 {response.status_code}"
        detail = client.get(f"/people/{sample_person['slug']}")
        assert detail.status_code == 200

    assert not writes, (
        "公開頁的 GET 產生了資料庫寫入（NOTE-005）：\n  " + "\n  ".join(writes)
    )


# ----------------------------------------------------------------------
# NOTE-007：稽核 IP 不提供「固定預設 salt」這個選項
# ----------------------------------------------------------------------
_TEST_SALT = "aP9x-high-entropy-test-salt-not-for-production-8Kq2"


def test_note007_no_hardcoded_default_salt():
    """程式不得為 IP salt 提供硬編碼預設值（NOTE-007）。

    固定預設 salt 會產生「可被完整反查、卻讓人誤以為安全」的假去識別化。
    IPv4 空間只有 2^32，salt 一旦可預測，雜湊就能被窮舉還原。
    """
    if os.environ.get("AUDIT_IP_SALT"):
        pytest.skip("環境已設定 AUDIT_IP_SALT，無法在此驗證預設值")

    assert audit_log_module._IP_SALT is None, (
        "未設定 AUDIT_IP_SALT 時 _IP_SALT 必須是 None。"
        "出現非 None 值代表有人加了硬編碼預設 salt，違反 NOTE-007。"
    )


def test_note007_without_salt_ip_is_not_recorded(monkeypatch):
    """沒有 salt 就完全不記錄 IP，而不是退回某種雜湊（NOTE-007）。"""
    monkeypatch.setattr(audit_log_module, "_IP_SALT", None)
    assert AuditLog.hash_ip("203.0.113.7") is None


def test_note007_with_salt_produces_irreversible_short_hash(monkeypatch):
    """提供 salt 時存 SHA-256 前 32 字元，且不得含明碼 IP（NOTE-007）。"""
    monkeypatch.setattr(audit_log_module, "_IP_SALT", _TEST_SALT)
    ip = "203.0.113.7"

    hashed = AuditLog.hash_ip(ip)

    assert hashed is not None
    assert len(hashed) == 32, "應截斷為 32 字元（128 bit）"
    assert all(c in "0123456789abcdef" for c in hashed), "應為十六進位摘要"
    assert ip not in hashed


def test_note007_hash_is_stable_per_source_but_differs_across_sources(monkeypatch):
    """同來源穩定、不同來源相異——這是 brute force 分析的前提（NOTE-007）。"""
    monkeypatch.setattr(audit_log_module, "_IP_SALT", _TEST_SALT)

    assert AuditLog.hash_ip("203.0.113.7") == AuditLog.hash_ip("203.0.113.7")
    assert AuditLog.hash_ip("203.0.113.7") != AuditLog.hash_ip("203.0.113.8")


def test_note007_salt_actually_participates_in_the_digest(monkeypatch):
    """換 salt 必須換出不同雜湊。

    若沒有這條，把 salt 拿掉不用（例如改成純 sha256(ip)）也會讓
    上面幾條測試全部通過——那正是 NOTE-007 要防的「假去識別化」。
    """
    ip = "203.0.113.7"

    monkeypatch.setattr(audit_log_module, "_IP_SALT", _TEST_SALT)
    first = AuditLog.hash_ip(ip)

    monkeypatch.setattr(audit_log_module, "_IP_SALT", _TEST_SALT + "-rotated")
    second = AuditLog.hash_ip(ip)

    assert first != second, "salt 未實際參與雜湊運算"


def test_note007_write_stores_null_ip_hash_when_salt_absent(app, monkeypatch):
    """端到端：沒有 salt 時 write() 寫入的那列 ip_hash 必須是 NULL（NOTE-007）。"""
    monkeypatch.setattr(audit_log_module, "_IP_SALT", None)

    with app.app_context():
        entry = AuditLog.write(action="login", ip_address="203.0.113.7")
        db.session.commit()

        stored = db.session.get(AuditLog, entry.id)
        assert stored.ip_hash is None


# ----------------------------------------------------------------------
# NOTE-008：稽核寫入失敗不得讓使用者的正常操作失敗
# ----------------------------------------------------------------------
def test_note008_oversized_summary_is_truncated_not_raised(app):
    """summary 過長時截斷並附省略號，不拋例外（NOTE-008）。

    這是**刻意不擋**的失敗：稽核是旁路關注點，讓它反過來擋掉
    使用者的正常操作，等於把可用性賠給了觀測性。
    """
    with app.app_context():
        entry = AuditLog.write(action="update", summary="超長內容" * 500)
        db.session.commit()

        stored = db.session.get(AuditLog, entry.id)
        assert stored.summary is not None
        assert len(stored.summary) < 500 * 4, "過長的 summary 應被截斷"
        assert stored.summary.endswith("…"), "截斷後應附省略號以示內容不完整"
