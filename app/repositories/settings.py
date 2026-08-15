# ============================================================
# NTUST SiPh Lab - Settings / Audit / Redirect Repository
#
# 上下游：
#   context processor -> get_site_settings() -> 所有 Template
#   Admin Dashboard -> recent_changes() -> AuditLog
#   Public 404 handler -> resolve_redirect() -> Redirect -> 301
#   blueprints/admin/routes.py -> list_redirects()
#
# 檔案路徑：
#   app/repositories/settings.py
#
# 建立日期：2026-08-14
# 最後重大修改：2026-08-14
# 版本：v1.0
#
# 模組定位與責任邊界：
#   SiteSetting（singleton）、AuditLog 與 Redirect 的查詢邊界。
#   這三者的共通點是「支援全站運作但不是內容主體」，
#   資料量小、查詢型態單純，故合併於同一 repository
#   （對應 SAI §9.4 目錄樹中的 repositories/settings.py）。
#
#   責任邊界（不得做的事）：
#     - 不得寫入 AuditLog（那是 AuditLog.write，由 service 呼叫）。
#     - 不得建立 Redirect（那是 Redirect.record，由 service 呼叫）。
#     - 不得 commit。
#
# 輸入 -> 處理 -> 輸出 Pipeline：
#   get_site_settings: 無輸入 -> SiteSetting.get() -> singleton 物件
#   recent_changes:    limit -> AuditLog 依時間倒序 -> list
#   resolve_redirect:  path -> Redirect.resolve() -> Redirect | None
#
# 主要 Function：
#   get_site_settings()
#   recent_changes(limit)
#   recent_security_events(limit)
#   resolve_redirect(path)
#   list_redirects()
#   count_redirects()
#
# 依賴套件：
#   sqlalchemy, app.extensions.db, app.models
#
# 環境變數：無。
#
# 資料庫使用方式：
#   audit_logs 依 created_at 索引倒序查詢；
#   redirects 依 old_path UNIQUE 索引查詢。
#
# Error Handling / Fallback：
#   get_site_settings() 永遠回傳物件（SiteSetting.get 保證），
#   讓 template 不需要 None 檢查。
#
# 特殊機制：
#   recent_changes 刻意只查 AuditLog 本體，不預載操作者。
#   理由：v1 為單一管理員（ADR-007），Dashboard 顯示「誰做的」
#   沒有資訊量，因此不需要 join。若未來導入多人 RBAC，
#   應在此加入 admin_user relationship 與 selectinload，
#   否則列表會出現 N+1。
#
# 已知限制與禁止事項：
#   1. 禁止提供刪除 AuditLog 的函式（稽核紀錄只增不刪，
#      見 models/audit_log.py 維護契約）。
#   2. 禁止在此提供修改 SiteSetting 的函式（寫入走 service）。
#
# 維護契約：
#   若 AuditLog 資料量成長到影響 Dashboard 效能，
#   應加入時間範圍過濾而非移除索引或改寫為 raw SQL。
#
# 驗證方式：
#   pytest tests/test_seo.py
#   pytest tests/test_auth.py
# ============================================================

from __future__ import annotations

from sqlalchemy import func, select

from app.extensions import db
from app.models.audit_log import AuditLog
from app.models.mixins import AuditAction
from app.models.redirect import Redirect
from app.models.site_setting import SiteSetting


def get_site_settings() -> SiteSetting:
    """取得全站設定 singleton。永遠回傳物件，不會是 None。"""
    return SiteSetting.get()


def recent_changes(limit: int = 10) -> list[AuditLog]:
    """Dashboard「Recent changes」最近 N 筆內容異動（SAI §7.2）。

    刻意排除純安全事件（login/logout/login_failed），
    因為 SAI §7.2 的 Recent changes 區塊是「內容」異動紀錄，
    登入事件混進來會把真正的內容變更淹沒。
    安全事件另由 recent_security_events() 提供。
    """
    content_actions = (
        AuditAction.CREATE,
        AuditAction.UPDATE,
        AuditAction.PUBLISH,
        AuditAction.ARCHIVE,
        AuditAction.GRADUATE,
        AuditAction.DELETE,
    )
    stmt = (
        select(AuditLog)
        .where(AuditLog.action.in_(content_actions))
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(limit)
    )
    return list(db.session.scalars(stmt).all())


def recent_security_events(limit: int = 10) -> list[AuditLog]:
    """最近的登入/密碼相關事件，供 Admin System 區塊檢視。

    用途：管理者可自行確認是否有異常登入嘗試
    （SAI §11.2 Brute force 的「audit」那一環）。
    """
    security_actions = (
        AuditAction.LOGIN,
        AuditAction.LOGIN_FAILED,
        AuditAction.LOGOUT,
        AuditAction.PASSWORD_CHANGE,
    )
    stmt = (
        select(AuditLog)
        .where(AuditLog.action.in_(security_actions))
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(limit)
    )
    return list(db.session.scalars(stmt).all())


def resolve_redirect(path: str | None) -> Redirect | None:
    """解析路徑的 redirect 目標（含鏈式壓平）。

    委派給 Redirect.resolve —— 邏輯放在 model 是因為
    鏈式壓平屬於 Redirect 的內在語意，而非查詢策略。
    """
    return Redirect.resolve(path)


def list_redirects(limit: int | None = None) -> list[Redirect]:
    """列出所有 redirect（新到舊），供 Admin 檢視。"""
    stmt = select(Redirect).order_by(Redirect.created_at.desc(), Redirect.id.desc())
    if limit:
        stmt = stmt.limit(limit)
    return list(db.session.scalars(stmt).all())


def count_redirects() -> int:
    """redirect 總數（Dashboard / 驗收腳本用）。"""
    return db.session.scalar(select(func.count(Redirect.id))) or 0


def count_audit_logs() -> int:
    """稽核紀錄總數（System health 顯示用）。"""
    return db.session.scalar(select(func.count(AuditLog.id))) or 0
