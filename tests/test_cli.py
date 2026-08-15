# ============================================================
# NTUST SiPh Lab - CLI Command Tests
#
# 上下游：
#   tests/conftest.py（app fixture）
#       -> app/cli.py（flask admin / check / seed 指令）
#       -> app/models/admin_user.py、audit_log.py
#
# 檔案路徑：tests/test_cli.py
# 建立日期：2026-08-15 / 版本：v1.0
#
# 模組定位（SAI §8.2、§11.1）：
#   CLI 是「初始管理員建立」與「密碼重設」的唯一途徑，
#   §11.1 明定 v1 不做 email reset。因此這條路徑失效
#   等同整個後台無法使用，必須有測試保護。
#
# 重點涵蓋：
#   1. 單一 admin 約束（§8.2：禁止第二個 active admin）
#   2. 密碼強度驗證
#   3. 憑證輸出的編碼安全（見 test_generate_survives_*）
#   4. 不得把明碼寫入 AuditLog（§8.8）
#
# 為什麼特別測「編碼」：
#   admin create --generate 會先 commit 帳號、再印出一次性密碼。
#   若輸出因主控台編碼（Windows cp950）而崩潰，帳號已存在但
#   密碼永遠遺失，且 §8.2 禁止再建立第二個 admin —— 使用者
#   會卡在無法登入也無法重建的狀態。此問題實際發生過，
#   因此以回歸測試鎖住。
#
# 驗證方式：
#   pytest tests/test_cli.py -v
# ============================================================

from __future__ import annotations

import io
import sys

import pytest

from app.cli import _ensure_utf8_output
from app.extensions import db
from app.models.admin_user import AdminUser
from app.models.audit_log import AuditLog


# ----------------------------------------------------------------------
# admin create
# ----------------------------------------------------------------------
def test_admin_create_makes_active_account(app):
    """建立帳號後應可用該密碼驗證。"""
    runner = app.test_cli_runner()
    result = runner.invoke(args=["admin", "create", "--username", "cliadmin"],
                           input="StrongPassphrase123\nStrongPassphrase123\n")

    assert result.exit_code == 0, result.output
    with app.app_context():
        user = AdminUser.find_by_username("cliadmin")
        assert user is not None
        assert user.is_active
        assert user.verify_password("StrongPassphrase123")


def test_admin_create_never_stores_plaintext(app):
    """DB 不得出現明碼（SAI §8.2）。"""
    runner = app.test_cli_runner()
    runner.invoke(args=["admin", "create", "--username", "cliadmin"],
                  input="StrongPassphrase123\nStrongPassphrase123\n")

    with app.app_context():
        user = AdminUser.find_by_username("cliadmin")
        assert "StrongPassphrase123" not in user.password_hash
        # AuditLog.summary 也不得洩漏密碼（§8.8）
        logs = db.session.query(AuditLog).all()
        for log in logs:
            assert "StrongPassphrase123" not in (log.summary or "")


def test_admin_create_rejects_second_active_admin(app):
    """SAI §8.2 / ADR-007：v1 僅允許單一 active admin。"""
    runner = app.test_cli_runner()
    runner.invoke(args=["admin", "create", "--username", "first"],
                  input="StrongPassphrase123\nStrongPassphrase123\n")

    result = runner.invoke(args=["admin", "create", "--username", "second"],
                           input="StrongPassphrase123\nStrongPassphrase123\n")

    assert result.exit_code != 0
    assert "單一管理員" in result.output or "已存在" in result.output
    with app.app_context():
        assert AdminUser.find_by_username("second") is None


def test_admin_create_rejects_weak_password(app):
    """密碼強度不足應失敗，且不得留下帳號。"""
    runner = app.test_cli_runner()
    result = runner.invoke(args=["admin", "create", "--username", "weak"],
                           input="short\nshort\n")

    assert result.exit_code != 0
    with app.app_context():
        assert AdminUser.find_by_username("weak") is None


# ----------------------------------------------------------------------
# 憑證輸出的編碼安全（回歸測試）
# ----------------------------------------------------------------------
def test_generate_prints_password_before_decorative_output(app):
    """--generate 必須輸出一次性密碼，且密碼在裝飾性訊息之前。

    順序很重要：若裝飾性訊息（含 ✔ 與中文）先輸出並崩潰，
    密碼就永遠不會出現。
    """
    runner = app.test_cli_runner()
    result = runner.invoke(args=["admin", "create", "--username", "genadmin", "--generate"])

    assert result.exit_code == 0, result.output
    assert "一次性密碼" in result.output

    # 密碼行必須早於「已建立管理員帳號」那行
    pw_index = result.output.index("一次性密碼")
    ok_index = result.output.index("已建立管理員帳號")
    assert pw_index < ok_index, f"密碼必須先輸出：\n{result.output}"

    # 取出密碼並確認真的可用（證明輸出的不是佔位字串）
    lines = [ln.strip() for ln in result.output.splitlines() if ln.strip()]
    password = lines[lines.index("一次性密碼（請立即妥善保存，不會再次顯示）：") + 1]
    with app.app_context():
        user = AdminUser.find_by_username("genadmin")
        assert user.verify_password(password)


def test_ensure_utf8_output_makes_non_utf8_stream_safe(monkeypatch):
    """回歸測試：非 UTF-8 主控台不得讓輸出崩潰。

    重現原始缺陷：以 cp950 編碼的串流輸出 '✔' 會拋
    UnicodeEncodeError。_ensure_utf8_output() 之後必須不再拋錯。
    """
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp950", errors="strict")
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(sys, "stderr", stream)

    # 修正前的行為：strict cp950 無法編碼 '✔'
    with pytest.raises(UnicodeEncodeError):
        stream.write("✔ 已建立管理員帳號")
        stream.flush()

    # 套用修正後不得再拋錯
    _ensure_utf8_output()
    sys.stdout.write("✔ 已建立管理員帳號：reviewadmin")
    sys.stdout.flush()


# ----------------------------------------------------------------------
# reset-password
# ----------------------------------------------------------------------
def test_reset_password_changes_credential(app):
    """重設後舊密碼失效、新密碼生效（SAI §11.1）。"""
    runner = app.test_cli_runner()
    runner.invoke(args=["admin", "create", "--username", "resetme"],
                  input="OriginalPassphrase1\nOriginalPassphrase1\n")

    result = runner.invoke(args=["admin", "reset-password", "--username", "resetme"],
                           input="ReplacementPassphrase2\nReplacementPassphrase2\n")

    assert result.exit_code == 0, result.output
    with app.app_context():
        user = AdminUser.find_by_username("resetme")
        assert not user.verify_password("OriginalPassphrase1")
        assert user.verify_password("ReplacementPassphrase2")


def test_reset_password_unknown_user_fails(app):
    runner = app.test_cli_runner()
    result = runner.invoke(args=["admin", "reset-password", "--username", "ghost"],
                           input="ReplacementPassphrase2\nReplacementPassphrase2\n")
    assert result.exit_code != 0
    assert "找不到帳號" in result.output


def test_admin_list_reports_no_accounts(app):
    runner = app.test_cli_runner()
    result = runner.invoke(args=["admin", "list"])
    assert result.exit_code == 0
    assert "尚未建立" in result.output
