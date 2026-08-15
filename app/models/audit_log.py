# ============================================================
# NTUST SiPh Lab - AuditLog Model
#
# 上下游：
#   blueprints/auth（登入/登出/失敗）-> AuditLog -> DB
#   services/person_service、research_service、settings（內容異動）
#       -> AuditLog -> DB
#   Admin Dashboard "Recent changes" -> repositories -> AuditLog
#
# 檔案路徑：
#   app/models/audit_log.py
#
# 建立日期：2026-08-14
# 最後重大修改：2026-08-14
# 版本：v1.0
#
# 模組定位與責任邊界：
#   記錄「管理端內容異動與安全事件」，讓重要操作可回溯
#   （SAI §1.1 成功定義、§8.8）。
#
#   SAI §19 明確界定：AuditLog 不是 application debug log 的替代品。
#   因此本表只記錄「誰、在什麼時間、對哪個實體、做了什麼」，
#   不記錄堆疊、request payload 或效能資訊。
#
#   責任邊界（不得做的事）：
#     - 不得儲存密碼、session cookie、SECRET_KEY 或完整 form payload
#       （SAI §19「No secret logging」）。
#     - 不得用來做業務查詢的資料來源（它是稽核紀錄，不是內容表）。
#
# 輸入 -> 處理 -> 輸出 Pipeline：
#   輸入：action、entity_type、entity_id、summary、admin_user、IP
#   處理：summary 長度截斷、IP 雜湊化
#   輸出：audit_logs row
#
# 主要 Class / Function：
#   AuditLog.write(...)   - 建立紀錄的唯一入口（不 commit）
#   AuditLog.hash_ip(ip)  - 以 SHA-256 雜湊 IP
#
# 依賴套件：
#   sqlalchemy, hashlib, app.extensions.db
#
# 環境變數：
#   AUDIT_IP_SALT（選用）。未設定時使用固定字串，
#   雜湊仍不可逆，但跨部署不可比對 —— 這是刻意的隱私預設。
#
# 資料庫使用方式：
#   audit_logs table。admin_user_id 為 FK 但使用 ON DELETE SET NULL，
#   確保刪除帳號不會連帶刪除稽核紀錄（稽核紀錄必須比帳號更長壽）。
#
# Error Handling / Fallback：
#   write() 不會因為 summary 過長而失敗，改為截斷並附省略號。
#   理由：稽核寫入失敗絕不應該讓使用者的正常操作失敗。
#
# 特殊機制（隱私）：
#   SAI §8.8 指出「依隱私政策決定是否保存完整 IP；v1 可只留必要
#   資訊」。本實作預設只存 SHA-256 雜湊前 32 字元：
#     - 仍可判斷「是否為同一來源的連續失敗」（brute force 分析）
#     - 但無法還原真實 IP，降低個資保存風險
#
# 已知限制與禁止事項：
#   1. 本表只增不改；禁止提供 UI 讓管理員編輯或刪除稽核紀錄。
#   2. 禁止在 summary 放入使用者輸入的原始 HTML（雖然顯示端會
#      escape，但稽核紀錄應為簡短描述而非內容副本）。
#   3. 無自動清理機制；資料量成長由備份與 DB 維運處理。
#
# 維護契約：
#   新增 action 值時必須同步更新 mixins.AuditAction.ALL 與
#   Alembic CheckConstraint，否則寫入會被 DB 拒絕而導致
#   「內容更新成功但稽核失敗」的 transaction rollback。
#
# 驗證方式：
#   pytest tests/test_auth.py::test_login_writes_audit_log
#   pytest tests/test_people.py::test_graduate_writes_audit_log
# ============================================================

from __future__ import annotations

import hashlib
import os
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db
from app.models.mixins import UtcDateTime, utcnow

#: summary 欄位的最大長度；超過則截斷。
_SUMMARY_MAX = 500

#: IP 雜湊使用的 salt。未設定環境變數時使用固定值 ——
#: 雜湊仍不可逆，只是失去跨部署可比對性，對隱私而言是更安全的預設。
_IP_SALT = os.environ.get("AUDIT_IP_SALT", "siph-lab-audit-v1")


class AuditLog(db.Model):
    """管理操作與安全事件稽核紀錄（SAI §8.8）。

    為什麼不繼承 TimestampMixin：
      稽核紀錄不可修改，因此不需要 updated_at。提供 updated_at
      反而暗示「這筆紀錄可以被改」，與稽核語意衝突。
    """

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    #: 操作者。SET NULL 讓稽核紀錄比帳號長壽。
    admin_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    action: Mapped[str] = mapped_column(String(40), nullable=False, index=True)

    #: person / research_output / site_setting / system
    entity_type: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    #: 短文字描述。禁止存密碼或敏感值。
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: IP 的 SHA-256 雜湊（前 32 字元），非明碼 IP。
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow, index=True
    )

    __table_args__ = (
        CheckConstraint(
            "action IN ('create', 'update', 'publish', 'archive', 'login', "
            "'login_failed', 'logout', 'password_change', 'graduate', 'delete')",
            name="ck_audit_logs_action",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - 僅除錯用
        return f"<AuditLog {self.action} {self.entity_type}:{self.entity_id}>"

    # ------------------------------------------------------------------
    # 建立紀錄
    # ------------------------------------------------------------------
    @staticmethod
    def hash_ip(ip_address: str | None) -> str | None:
        """把 IP 雜湊成不可逆字串。

        為什麼截斷到 32 字元：
          完整 SHA-256 為 64 字元。前 32 字元（128 bit）對「判斷是否
          為同一來源」已遠超過需求，且縮短欄位可降低儲存量。
          碰撞機率在本案的資料量級可忽略。
        """
        if not ip_address:
            return None
        digest = hashlib.sha256(f"{_IP_SALT}:{ip_address}".encode("utf-8")).hexdigest()
        return digest[:32]

    @classmethod
    def write(
        cls,
        action: str,
        entity_type: str | None = None,
        entity_id: int | None = None,
        summary: str | None = None,
        admin_user_id: int | None = None,
        ip_address: str | None = None,
    ) -> "AuditLog":
        """建立一筆稽核紀錄並加入 session（不 commit）。

        為什麼不 commit：
          稽核必須與被稽核的動作在同一個 transaction。
          若動作最終 rollback，稽核紀錄也必須一起消失，
          否則會出現「紀錄說改了，但資料沒改」的假稽核。

        Args:
            action: 必須是 AuditAction 允許值之一。
            summary: 過長會自動截斷，不會拋錯。

        Returns:
            尚未 commit 的 AuditLog 物件。
        """
        text = summary or ""
        if len(text) > _SUMMARY_MAX:
            text = text[: _SUMMARY_MAX - 1] + "…"

        entry = cls(
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            summary=text or None,
            admin_user_id=admin_user_id,
            ip_hash=cls.hash_ip(ip_address),
        )
        db.session.add(entry)
        return entry
