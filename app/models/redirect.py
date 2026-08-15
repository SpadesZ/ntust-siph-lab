# ============================================================
# NTUST SiPh Lab - Redirect Model
#
# 上下游：
#   PersonService / ResearchService（slug 變更）-> Redirect -> DB
#   Public Route 404 handler -> RedirectService.resolve() -> Redirect
#       -> 301 到 new_path（AC-10）
#   scripts/verify_migration.py -> 檢查舊 URL 不產生無意義 404
#
# 檔案路徑：
#   app/models/redirect.py
#
# 建立日期：2026-08-14
# 最後重大修改：2026-08-14
# 版本：v1.0
#
# 模組定位與責任邊界：
#   保存「舊路徑 -> 新路徑」的永久對應，實作 SAI §4.2
#   「slug 一旦公開即視為永久識別；更名時建立 Redirect 記錄，
#   不直接讓舊 URL 404」。
#
#   責任邊界（不得做的事）：
#     - 不得在此決定何時建立 redirect（那是 service 層，
#       因為需要判斷 slug 是否曾公開過）。
#     - 不得在此處理 HTTP response（那是 blueprint）。
#
# 輸入 -> 處理 -> 輸出 Pipeline：
#   輸入：old_path、new_path、reason
#   處理：路徑正規化（去 query、確保前導斜線、去尾斜線）、
#         鏈式 redirect 壓平、自我指向偵測
#   輸出：Redirect row；查詢時回傳目標路徑
#
# 主要 Class / Function：
#   Redirect.normalize_path(path)  - 路徑正規化（唯一入口）
#   Redirect.resolve(path)         - 解析最終目標（含鏈式壓平）
#   Redirect.record(old, new, ...) - 建立或更新對應（不 commit）
#
# 依賴套件：
#   sqlalchemy, app.extensions.db
#
# 環境變數：
#   無。
#
# 資料庫使用方式：
#   redirects table，old_path UNIQUE（SAI §8.9）。
#
# Error Handling / Fallback：
#   - resolve() 對 None/空字串回 None，不 raise。
#   - 偵測到 old_path == new_path 時拒絕建立，避免無限迴圈。
#   - 鏈式解析有 _MAX_HOPS 上限，即使資料出現環也會安全終止。
#
# 特殊機制：
#   鏈式壓平（chain flattening）：若 A->B 已存在，之後又建立 B->C，
#   resolve(A) 會回傳 C 而不是 B。為什麼重要：連續兩次改 slug 若
#   不壓平，舊連結會經歷兩次 301，浪費 crawl budget 且部分爬蟲會
#   放棄（SAI §12.1「舊 URL 不造成無意義 404」的精神延伸）。
#
# 已知限制與禁止事項：
#   1. 只處理路徑（path），不含 query string。查詢參數不應改變
#      canonical（SAI §4.2），因此 redirect 時一律丟棄 query。
#   2. 禁止建立指向 /admin/* 的 redirect。
#   3. status_code 僅允許 301 / 302；永久變更預設 301（SAI §8.7）。
#
# 維護契約：
#   任何會改變公開 slug 的程式路徑，都必須在同一 transaction 內
#   呼叫 record()。漏掉會造成既有外部連結與搜尋結果直接 404，
#   這是不可逆的 SEO 損失。
#
# 驗證方式：
#   pytest tests/test_research.py::test_slug_change_creates_redirect
#   pytest tests/test_seo.py
# ============================================================

from __future__ import annotations

from sqlalchemy import CheckConstraint, Integer, String, select
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db
from app.models.mixins import TimestampMixin

#: 鏈式 redirect 解析的最大跳數，防止資料成環時無限迴圈。
_MAX_HOPS = 10


class Redirect(TimestampMixin, db.Model):
    """舊路徑到新路徑的永久對應（SAI §8.7）。"""

    __tablename__ = "redirects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    #: 正規化後的舊路徑，例如 "/research/old-slug"。
    old_path: Mapped[str] = mapped_column(String(500), nullable=False, unique=True, index=True)
    new_path: Mapped[str] = mapped_column(String(500), nullable=False)

    #: 永久變更預設 301（SAI §8.7）。
    status_code: Mapped[int] = mapped_column(Integer, nullable=False, default=301)

    #: slug_changed / migration / manual（SAI §8.7）。
    reason: Mapped[str | None] = mapped_column(String(60), nullable=True)

    __table_args__ = (
        CheckConstraint("status_code IN (301, 302)", name="ck_redirects_status_code"),
    )

    def __repr__(self) -> str:  # pragma: no cover - 僅除錯用
        return f"<Redirect {self.old_path!r} -> {self.new_path!r} ({self.status_code})>"

    # ------------------------------------------------------------------
    # 路徑正規化
    # ------------------------------------------------------------------
    @staticmethod
    def normalize_path(path: str | None) -> str | None:
        """把任意輸入正規化為可比對的路徑字串。

        規則：
          1. 去除 query string 與 fragment（SAI §4.2：查詢參數不可
             產生新的 canonical，因此也不參與 redirect 比對）。
          2. 確保有前導斜線。
          3. 去除尾斜線（根路徑 "/" 除外）。

        回傳 None 表示輸入不是可用路徑，呼叫端應略過。
        """
        if not path:
            return None
        value = str(path).strip()
        if not value:
            return None

        # 丟棄 fragment 與 query。
        for sep in ("#", "?"):
            if sep in value:
                value = value.split(sep, 1)[0]

        if not value.startswith("/"):
            value = "/" + value

        if len(value) > 1:
            value = value.rstrip("/") or "/"

        return value

    # ------------------------------------------------------------------
    # 查詢與建立
    # ------------------------------------------------------------------
    @classmethod
    def resolve(cls, path: str | None) -> "Redirect | None":
        """解析路徑的最終 redirect 目標（含鏈式壓平）。

        回傳的是「起點那一筆 Redirect 物件」，但其 new_path 已被
        就地更新為鏈的終點嗎？—— 不是。本方法不修改資料庫，
        而是回傳一個 detached 的結果物件語意：呼叫端應使用
        `resolve(...).new_path` 取得最終目標。實作上我們追蹤到
        鏈尾後，回傳「最後一跳」的 Redirect，其 new_path 即終點。

        設計理由：
          不在讀取路徑上寫資料庫，讓 GET 保持唯讀（避免 read replica
          或 restore drill 時失敗）。壓平寫回由 record() 在寫入時完成。
        """
        normalized = cls.normalize_path(path)
        if not normalized:
            return None

        current = db.session.scalar(select(cls).where(cls.old_path == normalized))
        if current is None:
            return None

        # 追蹤鏈尾：A->B, B->C 時，輸入 A 應得到 C。
        visited = {normalized}
        hops = 0
        while hops < _MAX_HOPS:
            next_link = db.session.scalar(select(cls).where(cls.old_path == current.new_path))
            if next_link is None or next_link.new_path in visited:
                break
            visited.add(next_link.old_path)
            current = next_link
            hops += 1

        return current

    @classmethod
    def record(
        cls,
        old_path: str,
        new_path: str,
        reason: str = "slug_changed",
        status_code: int = 301,
    ) -> "Redirect | None":
        """建立或更新一筆 redirect。呼叫端負責 commit。

        為什麼不 commit：
          SAI §9.3 要求 slug 變更、Redirect 建立與 AuditLog 寫入
          必須在同一 transaction。若這裡自行 commit，
          後續步驟失敗時會留下「有 redirect 但 slug 沒改」的錯誤狀態。

        行為：
          - old_path 與 new_path 正規化後相同 -> 不建立，回 None。
          - old_path 已存在 -> 更新其 new_path（等同壓平舊鏈）。
          - 同時把所有指向 old_path 的既有 redirect 一併改指 new_path，
            避免產生多跳鏈。

        Returns:
            建立/更新後的 Redirect；不需要建立時回 None。
        """
        old_norm = cls.normalize_path(old_path)
        new_norm = cls.normalize_path(new_path)

        if not old_norm or not new_norm or old_norm == new_norm:
            return None

        # 安全性：不建立指向管理後台的 redirect。
        if new_norm.startswith("/admin"):
            return None

        existing = db.session.scalar(select(cls).where(cls.old_path == old_norm))
        if existing is not None:
            existing.new_path = new_norm
            existing.reason = reason
            existing.status_code = status_code
            record = existing
        else:
            record = cls(
                old_path=old_norm,
                new_path=new_norm,
                reason=reason,
                status_code=status_code,
            )
            db.session.add(record)

        # 壓平：把原本指向 old_norm 的所有 redirect 直接改指 new_norm。
        # 這確保任何舊 URL 最多只需要一次 301。
        inbound = db.session.scalars(select(cls).where(cls.new_path == old_norm)).all()
        for link in inbound:
            if link.old_path != new_norm:
                link.new_path = new_norm

        return record
