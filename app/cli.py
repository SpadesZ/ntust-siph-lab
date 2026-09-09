# ============================================================
# NTUST SiPh Lab - Flask CLI Commands
#
# 上下游：
#   終端機 `flask admin create` -> AdminUser -> DB
#   終端機 `flask admin reset-password` -> AdminUser.set_password
#   終端機 `flask seed legacy` -> scripts/seed_from_google_sites.py
#   終端機 `flask check publish` -> PublishValidator（批次品質檢查）
#
# 檔案路徑：
#   app/cli.py
#
# 建立日期：2026-08-14
# 最後重大修改：2026-08-14
# 版本：v1.0
#
# 模組定位與責任邊界：
#   提供「不透過網頁就必須能做」的維運操作。SAI §8.2 指定
#   初始帳號以 CLI 建立（flask admin create），§11.1 指定
#   密碼重設走 server CLI 而非 email 流程。
#
#   責任邊界（不得做的事）：
#     - 不得提供刪除全部資料的破壞性指令。
#     - 不得從環境變數讀取管理員明碼密碼
#       （SAI 附錄 B：Never store admin plaintext password in env）。
#     - 不得把密碼寫入 log 或 AuditLog.summary。
#
# 輸入 -> 處理 -> 輸出 Pipeline：
#   admin create: 互動輸入帳號/密碼（或 --generate 隨機產生）
#     -> AdminUser.set_password（scrypt）-> commit -> 印出帳號
#        （隨機密碼只印一次，不儲存）
#
# 主要 Function：
#   register_cli_commands(app)
#   admin create / admin reset-password / admin list
#   check publish
#   seed legacy
#
# 依賴套件：
#   click, flask, app.models, app.services
#
# 環境變數：
#   不讀取任何密碼相關環境變數（刻意）。
#
# 資料庫使用方式：
#   admin_users、audit_logs。本模組自行 commit。
#
# Error Handling / Fallback：
#   - 密碼不符強度要求時顯示原因並要求重新輸入（互動模式）。
#   - 已存在 active admin 時拒絕再建立（SAI §8.2 應用層約束），
#     並提示改用 reset-password。
#
# 特殊機制（密碼輸入）：
#   使用 click.prompt(hide_input=True, confirmation_prompt=True)，
#   密碼不會出現在終端機畫面，也不會進入 shell history
#   （相對於 --password 參數）。--generate 模式產生
#   secrets.token_urlsafe 隨機密碼並只顯示一次。
#
# 特殊機制（輸出編碼與憑證優先）：
#   _ensure_utf8_output() 在註冊指令時把 stdout/stderr 轉為
#   UTF-8 且 errors="replace"；_emit_credential() 保證一次性密碼
#   先於任何裝飾性訊息輸出。兩者共同防止「帳號已建立但密碼
#   永遠不顯示」的憑證遺失（曾在 Windows cp950 主控台實際發生）。
#   詳細理由見兩個函式的 docstring。
#
# 已知限制與禁止事項：
#   1. 禁止新增 --password 明碼參數（會留在 shell history 與
#      process list 中）。若自動化確實需要，應改為讀取
#      Secret Manager 而非命令列參數。
#   2. reset-password 不需要舊密碼 —— 這是刻意的：
#      這個指令的使用情境正是「忘記密碼」，且執行者已具備
#      伺服器存取權（等同最高權限）。
#
# 維護契約：
#   新增指令時，任何會修改資料的操作都必須寫 AuditLog，
#   並在 docs/local-development.md 說明用法。
#
# 驗證方式：
#   flask admin create --generate
#   flask admin list
#   pytest tests/test_cli.py
# ============================================================

from __future__ import annotations

import secrets
import sys

import click
from flask.cli import AppGroup

from app.extensions import db
from app.models.admin_user import AdminUser
from app.models.audit_log import AuditLog
from app.models.mixins import AuditAction

#: 隨機密碼的位元組長度（token_urlsafe 產出約 1.3 倍字元數）。
_GENERATED_PASSWORD_BYTES = 18


def _ensure_utf8_output() -> None:
    """讓 CLI 輸出不會因主控台編碼而失敗。

    為什麼這是安全性問題而不只是體驗問題：
      Windows 主控台預設可能是 cp950/cp1252 等非 UTF-8 編碼。
      本模組的訊息含中文與 ✔ 等字元，在這些編碼下 click.secho
      會拋 UnicodeEncodeError。

      致命之處在於崩潰的「時間點」：admin create --generate 會先
      db.session.commit() 寫入帳號，再印出一次性密碼。若印出
      成功訊息時崩潰，帳號已經存在但密碼永遠不會顯示，
      而 SAI §8.2 又禁止建立第二個 active admin ——
      使用者會得到一個無法登入也無法重建的帳號。
      （此問題實際發生於 Windows cp950 環境。）

      errors="replace" 保證輸出永不因編碼中斷：寧可少數字元
      顯示為 '?'，也不能讓憑證遺失。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):  # pragma: no cover - 依終端機而定
            # 少數被重導向的串流不支援 reconfigure；
            # 此時維持原設定即可，下游仍有 errors="replace" 的保護。
            pass


def _emit_credential(label: str, password: str) -> None:
    """輸出一次性密碼。

    刻意先於任何裝飾性訊息呼叫（見 _ensure_utf8_output 的說明）：
    憑證是這個指令唯一不可重現的產出，必須最優先送出。
    """
    click.echo(label)
    click.echo(f"  {password}")


def register_cli_commands(app) -> None:
    """把所有 CLI group 註冊到 app。"""
    _ensure_utf8_output()
    admin_cli = AppGroup("admin", help="管理員帳號維護（SAI §8.2、§11.1）。")
    check_cli = AppGroup("check", help="內容品質與發布門檻檢查。")
    seed_cli = AppGroup("seed", help="母站內容遷移與初始化。")

    # ------------------------------------------------------------------
    # admin
    # ------------------------------------------------------------------
    @admin_cli.command("create")
    @click.option("--username", prompt=True, help="管理員登入帳號。")
    @click.option(
        "--generate",
        is_flag=True,
        default=False,
        help="自動產生高強度隨機密碼（只顯示一次，不儲存明碼）。",
    )
    def create_admin(username: str, generate: bool):
        """建立初始管理員帳號。

        SAI §8.2：v1 禁止新增第二個 active admin。
        已存在時本指令會拒絕並提示改用 reset-password。
        """
        normalized = AdminUser.normalize_username(username)
        if not normalized:
            raise click.ClickException("帳號不得為空。")

        if AdminUser.find_by_username(normalized) is not None:
            raise click.ClickException(
                f"帳號 {normalized!r} 已存在。若要重設密碼請執行："
                " flask admin reset-password"
            )

        if AdminUser.active_count() >= 1:
            raise click.ClickException(
                "已存在啟用中的管理員帳號。v1 僅支援單一管理員（ADR-007 / SAI §8.2）。"
                " 若要更換密碼請使用 flask admin reset-password。"
            )

        if generate:
            password = secrets.token_urlsafe(_GENERATED_PASSWORD_BYTES)
        else:
            password = click.prompt(
                "密碼（至少 12 字元，輸入時不顯示）",
                hide_input=True,
                confirmation_prompt=True,
            )

        user = AdminUser(username=normalized)
        try:
            user.set_password(password)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc

        db.session.add(user)
        db.session.flush()

        AuditLog.write(
            action=AuditAction.CREATE,
            entity_type="system",
            entity_id=user.id,
            summary=f"以 CLI 建立管理員帳號 {normalized}",
            admin_user_id=user.id,
        )
        db.session.commit()

        # 憑證優先輸出（見 _emit_credential 的說明）。
        if generate:
            _emit_credential("一次性密碼（請立即妥善保存，不會再次顯示）：", password)

        click.secho(f"✔ 已建立管理員帳號：{normalized}", fg="green")
        click.echo("  登入位置：/admin/login")

    @admin_cli.command("reset-password")
    @click.option("--username", prompt=True, help="要重設密碼的帳號。")
    @click.option("--generate", is_flag=True, default=False, help="自動產生隨機密碼。")
    def reset_password(username: str, generate: bool):
        """重設管理員密碼（SAI §11.1：v1 不做 email reset）。"""
        user = AdminUser.find_by_username(username)
        if user is None:
            raise click.ClickException(f"找不到帳號：{username}")

        if generate:
            password = secrets.token_urlsafe(_GENERATED_PASSWORD_BYTES)
        else:
            password = click.prompt(
                "新密碼（至少 12 字元，輸入時不顯示）",
                hide_input=True,
                confirmation_prompt=True,
            )

        try:
            user.set_password(password)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc

        AuditLog.write(
            action=AuditAction.PASSWORD_CHANGE,
            entity_type="system",
            entity_id=user.id,
            summary=f"以 CLI 重設帳號 {user.username} 的密碼",
            admin_user_id=user.id,
        )
        db.session.commit()

        # 憑證優先輸出（見 _emit_credential 的說明）。
        if generate:
            _emit_credential("一次性密碼（請立即妥善保存）：", password)

        click.secho(f"✔ 已重設 {user.username} 的密碼。", fg="green")

    @admin_cli.command("list")
    def list_admins():
        """列出所有管理員帳號（不含任何密碼資訊）。"""
        from sqlalchemy import select

        from app.utils.dates import format_datetime

        users = db.session.scalars(select(AdminUser).order_by(AdminUser.id)).all()
        if not users:
            click.echo("尚未建立任何管理員帳號。請執行：flask admin create")
            return

        for user in users:
            status = "啟用" if user.is_active else "停用"
            last_login = format_datetime(user.last_login_at, fallback="從未登入")
            click.echo(f"[{user.id}] {user.username}　{status}　最後登入：{last_login}")

    # ------------------------------------------------------------------
    # check
    # ------------------------------------------------------------------
    @check_cli.command("publish")
    def check_publish():
        """批次檢查所有內容的發布門檻（SAI 附錄 C）。

        用途：cutover 前的整體品質確認。
        回傳非零 exit code 表示有阻擋級問題，可用於 CI。
        """
        from sqlalchemy import select

        from app.models.mixins import PublishStatus
        from app.models.person import Person
        from app.models.research_output import ResearchOutput
        from app.services.publish_validator import PublishValidator

        blocking = 0
        warnings = 0

        people = db.session.scalars(
            select(Person).where(Person.publish_status == PublishStatus.PUBLISHED)
        ).all()
        for person in people:
            result = PublishValidator.validate_person(person)
            for issue in result.errors:
                blocking += 1
                click.secho(f"[ERROR] 人物 {person.slug}：{issue.message}", fg="red")
            for issue in result.warnings:
                warnings += 1
                click.secho(f"[WARN ] 人物 {person.slug}：{issue.message}", fg="yellow")

        outputs = db.session.scalars(
            select(ResearchOutput).where(
                ResearchOutput.publish_status == PublishStatus.PUBLISHED
            )
        ).all()
        for output in outputs:
            result = PublishValidator.validate_research(output)
            for issue in result.errors:
                blocking += 1
                click.secho(f"[ERROR] 成果 {output.slug}：{issue.message}", fg="red")
            for issue in result.warnings:
                warnings += 1
                click.secho(f"[WARN ] 成果 {output.slug}：{issue.message}", fg="yellow")

        click.echo(
            f"\n檢查完成：已發布人物 {len(people)} 筆、成果 {len(outputs)} 筆；"
            f"阻擋問題 {blocking} 項、提醒 {warnings} 項。"
        )
        if blocking:
            raise SystemExit(1)

    # ------------------------------------------------------------------
    # seed
    # ------------------------------------------------------------------
    @seed_cli.command("legacy")
    @click.option(
        "--force",
        is_flag=True,
        default=False,
        help="即使資料庫已有內容仍執行（會更新既有 legacy_id 對應的資料）。",
    )
    def seed_legacy(force: bool):
        """匯入母站 Legacy Baseline（LC-001~LC-019）。

        實際邏輯在 scripts/seed_from_google_sites.py，
        此指令只是讓它能在 app context 中執行。
        """
        from scripts.seed_from_google_sites import run_seed

        summary = run_seed(force=force)
        click.secho("✔ 母站內容匯入完成：", fg="green")
        for line in summary:
            click.echo(f"  {line}")

    @seed_cli.command("publications")
    @click.option(
        "--force",
        is_flag=True,
        default=False,
        help="已存在（相同 DOI）時仍更新書目欄位。",
    )
    def seed_publications(force: bool):
        """匯入教授已查證的論文著作。

        與 `seed legacy` 是不同來源：母站沒有研究成果資料，
        這些論文由研究室提供清單並經 Crossref 查證
        （SAI §2.3「需另有 Lab 確認來源」）。

        實際邏輯在 scripts/seed_publications.py。
        """
        from scripts.seed_publications import run_seed as run_publication_seed

        try:
            summary = run_publication_seed(force=force)
        except RuntimeError as exc:
            raise click.ClickException(str(exc)) from exc

        db.session.commit()
        click.secho("✔ 論文著作匯入完成：", fg="green")
        for line in summary:
            click.echo(f"  {line}")

    @seed_cli.command("equipment")
    @click.option(
        "--force",
        is_flag=True,
        default=False,
        help="已存在（相同 slug）時仍更新描述欄位（不動發布狀態）。",
    )
    def seed_equipment(force: bool):
        """匯入有公開出處的設備／設施資料（一律為 draft）。

        資料來自台科大官網新聞稿，內容是「所屬中心的設施」與
        「校外共享平台」，不是實驗室自有設備 —— 後者網路上查不到，
        只能由教授提供（SAI §2.3：不得推測產生內容）。

        實際邏輯在 scripts/seed_equipment.py。
        """
        from scripts.seed_equipment import run_seed as run_equipment_seed

        try:
            summary = run_equipment_seed(force=force)
        except RuntimeError as exc:
            raise click.ClickException(str(exc)) from exc

        db.session.commit()
        click.secho("✔ 設備資料匯入完成：", fg="green")
        for line in summary:
            click.echo(f"  {line}")

    @seed_cli.command("editorial")
    @click.option(
        "--force",
        is_flag=True,
        default=False,
        help="連已有內容的欄位也覆寫（會蓋掉管理者在後台的修改）。",
    )
    def seed_editorial(force: bool):
        """匯入編輯文案（首頁導言、關於研究室導言）。

        與 `seed legacy`、`seed publications` 又是不同來源：
        這些句子母站沒有，是依既有可查證資料撰寫的文案。
        每一則的依據記在 scripts/seed_editorial.py 的 provenance 註解，
        核准紀錄在 legacy/google_sites/content_signoff.md §2.3。

        預設不覆寫已有內容 —— 管理者在後台改過的文字優先於本檔。
        """
        from scripts.seed_editorial import run_seed as run_editorial_seed

        summary = run_editorial_seed(force=force)
        db.session.commit()
        click.secho("✔ 編輯文案匯入完成：", fg="green")
        for line in summary:
            click.echo(f"  {line}")

    app.cli.add_command(admin_cli)
    app.cli.add_command(check_cli)
    app.cli.add_command(seed_cli)
