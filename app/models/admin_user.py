# ============================================================
# NTUST SiPh Lab - AdminUser Model
#
# 上下游：
#   scripts/create_admin.py / flask admin create -> AdminUser -> DB
#   blueprints/auth/routes.py -> AdminUser.verify_password() -> session
#   extensions._load_admin_user() -> AdminUser -> current_user
#   AdminUser -> AuditLog.admin_user_id
#
# 檔案路徑：
#   app/models/admin_user.py
#
# 建立日期：2026-08-14
# 最後重大修改：2026-08-14
# 版本：v1.0
#
# 模組定位與責任邊界：
#   單一管理員帳號實體（ADR-007 / SAI §8.2）。負責密碼雜湊的
#   設定與驗證，以及 Flask-Login 所需的介面。
#
#   責任邊界（不得做的事）：
#     - 不得在此實作登入頻率限制（那是 blueprints/auth + limiter）。
#     - 不得在此 commit（由 auth route / service 決定交易邊界）。
#     - 不得提供任何「取回明碼」的方法。
#
# 輸入 -> 處理 -> 輸出 Pipeline：
#   set_password: 明碼 -> werkzeug generate_password_hash(scrypt)
#                 -> password_hash 欄位 + password_changed_at
#   verify_password: 明碼 -> check_password_hash -> bool
#
# 主要 Class / Function：
#   AdminUser.set_password(raw)      - 設定密碼（唯一寫入 hash 的入口）
#   AdminUser.verify_password(raw)   - 驗證密碼
#   AdminUser.touch_login()          - 更新 last_login_at
#   AdminUser.active_count()         - 目前 active admin 數量
#
# 依賴套件：
#   werkzeug.security, flask_login.UserMixin, sqlalchemy
#
# 環境變數：
#   無。初始密碼由 CLI 互動輸入或隨機產生，絕不從 env 讀取
#   （SAI 附錄 B：Never store admin plaintext password in env or repository）。
#
# 資料庫使用方式：
#   admin_users table。username 具 UNIQUE 索引。
#   v1 由應用層限制只能有一個 active admin（SAI §8.2「帳號數量約束」），
#   schema 本身不加死約束以保留未來 RBAC 擴充空間。
#
# Error Handling / Fallback：
#   - verify_password 對空密碼或空 hash 一律回 False，
#     不讓「hash 欄位為空」意外變成可登入。
#   - set_password 對空白密碼直接 raise ValueError，避免建立
#     不可能登入卻看似成功的帳號。
#
# 特殊機制：
#   密碼雜湊使用 Werkzeug 當前預設演算法（scrypt）。
#   刻意不寫死 method 參數，讓 Werkzeug 升級時自動採用更強預設
#   （SAI §11.1「當前安全預設」）。既有 hash 仍可驗證，因為
#   check_password_hash 會依 hash 字串前綴自行判斷演算法。
#
# 已知限制與禁止事項：
#   1. v1 不提供 email 重設密碼流程（SAI §11.1）；
#      忘記密碼一律以 CLI `flask admin reset-password` 處理。
#   2. 禁止在 log、flash message、AuditLog.summary 出現密碼或 hash。
#   3. 禁止新增第二個 active admin（應用層由 CLI 檢查）。
#
# 維護契約：
#   1. 任何新增的認證欄位（如 TOTP secret）都必須是「不可回復」形式。
#   2. 修改 set_password 時，必須確認既有 hash 仍能通過
#      verify_password，否則現有管理員會被鎖在門外。
#
# 驗證方式：
#   pytest tests/test_auth.py
# ============================================================

from __future__ import annotations

from datetime import datetime

from flask_login import UserMixin
from sqlalchemy import Boolean, Integer, String, select
from sqlalchemy.orm import Mapped, mapped_column
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db
from app.models.mixins import TimestampMixin, UtcDateTime, utcnow


class AdminUser(UserMixin, TimestampMixin, db.Model):
    """單一管理員帳號（SAI §8.2）。

    為什麼繼承 UserMixin：
      提供 Flask-Login 需要的 is_authenticated / is_anonymous /
      get_id()。注意 UserMixin 的 is_active 預設回 True，
      我們以真實欄位覆寫，確保停用帳號無法登入。
    """

    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    #: 登入帳號。儲存前一律 lower/strip 正規化（見 normalize_username），
    #: 避免 "Admin" 與 "admin" 被視為兩個帳號。
    username: Mapped[str] = mapped_column(String(80), nullable=False, unique=True, index=True)

    #: 只存 hash。長度 255 足以容納 scrypt 輸出並保留演算法升級空間。
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    last_login_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    #: 安全管理用；SAI §8.2 列為 Required。
    password_changed_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow
    )

    def __repr__(self) -> str:  # pragma: no cover - 僅除錯用
        return f"<AdminUser {self.username!r} active={self.is_active}>"

    # ------------------------------------------------------------------
    # 密碼處理
    # ------------------------------------------------------------------
    @staticmethod
    def normalize_username(raw: str) -> str:
        """正規化帳號字串。

        為什麼：SAI §8.2 要求 username "unique, normalized"。
        若不正規化，UNIQUE 索引無法阻止 "admin" 與 " Admin " 並存，
        會造成兩個看似同一人的帳號。
        """
        return (raw or "").strip().lower()

    def set_password(self, raw_password: str) -> None:
        """設定密碼並記錄變更時間。

        這是「唯一」允許寫入 password_hash 的入口。
        任何其他地方直接指派 password_hash 都視為違反維護契約，
        因為會跳過長度檢查與 password_changed_at 更新。

        Raises:
            ValueError: 密碼為空或長度不足 12 字元。

        為什麼是 12 字元：
          SAI §11.2 對 brute force 的對策是「rate limit + 強密碼 + audit」。
          rate limit 已由 limiter 提供，此處負責「強密碼」那一半。
        """
        if raw_password is None or raw_password.strip() == "":
            raise ValueError("密碼不得為空。")
        if len(raw_password) < 12:
            raise ValueError("密碼長度至少 12 字元（SAI §11.2 強密碼要求）。")

        # 不指定 method，讓 Werkzeug 使用當前安全預設（SAI §11.1）。
        self.password_hash = generate_password_hash(raw_password)
        self.password_changed_at = utcnow()

    def verify_password(self, raw_password: str) -> bool:
        """驗證密碼是否正確。

        Fallback 行為：任一輸入為空即回 False，
        避免空 hash 或空密碼造成非預期通過。
        """
        if not raw_password or not self.password_hash:
            return False
        return check_password_hash(self.password_hash, raw_password)

    # ------------------------------------------------------------------
    # 狀態維護
    # ------------------------------------------------------------------
    def touch_login(self) -> None:
        """記錄成功登入時間。呼叫端負責 commit。"""
        self.last_login_at = utcnow()

    # ------------------------------------------------------------------
    # 查詢輔助
    # ------------------------------------------------------------------
    @classmethod
    def find_by_username(cls, raw_username: str) -> "AdminUser | None":
        """依正規化帳號查詢。找不到回 None（不 raise）。"""
        username = cls.normalize_username(raw_username)
        if not username:
            return None
        return db.session.scalar(select(cls).where(cls.username == username))

    @classmethod
    def active_count(cls) -> int:
        """目前 active admin 數量。

        用途：CLI 建立帳號時據此執行 SAI §8.2 的
        「v1 禁止新增第二個 active admin」應用層約束。
        """
        return db.session.scalar(
            select(db.func.count()).select_from(cls).where(cls.is_active.is_(True))
        ) or 0
