# ============================================================
# NTUST SiPh Lab - Equipment Repository
#
# 上下游：
#   models/equipment.py
#     -> repositories/equipment.py（本檔）
#     -> blueprints/public/routes.py::equipment_index（分組後交給模板）
#     -> blueprints/admin/routes.py（後台列表與統計）
#
# 檔案路徑：
#   app/repositories/equipment.py
#
# 建立日期：2026-09-09 / 版本：v1.0
#
# 模組定位與責任邊界：
#   所有 equipment 表的查詢集中於此（SAI §9.1：Jinja 不做複雜 query）。
#
#   責任邊界（不得做的事）：
#     - 不得在此組 HTML 或 SEO 字串。
#     - 不得回傳未過濾 publish_status 的資料給公開頁。
#
# 主要 Function：
#   list_published_grouped() - 依 ownership 分組的公開設備（前台主查詢）
#   list_published()         - 全部已發布設備（sitemap / 統計用）
#   get_by_slug()            - 單筆查詢
#   admin_list()             - 後台列表（含 draft）
#   count_by_status()        - Dashboard 內容狀態統計
#   needs_attention()        - Dashboard 內容品質提醒
#
# 資料庫使用方式：
#   查詢走 ix_equipment_publish_ownership 複合索引
#   （publish_status, ownership, sort_order）。
#
# 已知限制：
#   目前設備數量是個位數，list_published_grouped 一次撈全部再於
#   Python 分組，不做分頁。若日後成長到數十筆以上再考慮。
#
# 驗證方式：
#   pytest tests/test_equipment.py
# ============================================================

from __future__ import annotations

from sqlalchemy import func, select

from app.extensions import db
from app.models.equipment import Equipment
from app.models.mixins import EquipmentOwnership, PublishStatus


def _public_stmt():
    """公開頁的基礎查詢：只取 published，依排序欄位排列。"""
    return (
        select(Equipment)
        .where(Equipment.publish_status.in_(PublishStatus.PUBLIC))
        .order_by(Equipment.sort_order.asc(), Equipment.id.asc())
    )


def list_published() -> list[Equipment]:
    """全部已發布設備（未分組）。"""
    return list(db.session.execute(_public_stmt()).scalars().all())


def list_published_grouped() -> list[tuple[str, list[Equipment]]]:
    """依 ownership 分組的已發布設備。

    回傳 [(ownership, [Equipment, ...]), ...]，順序固定為
    EquipmentOwnership.DISPLAY_ORDER（由最貼近實驗室到最外圍）。

    **空的分組不會出現在回傳值中**。這很重要：目前 lab 層級一筆
    都沒有（實驗室自有設備尚未由教授提供），若回傳空清單讓模板
    渲染出一個「本實驗室設備」標題底下空無一物，讀者會以為是
    網站壞了，而不是資料還沒填。沒有資料時就整組不出現，由頁面
    層級的 empty state 統一說明。
    """
    items = list_published()
    grouped: list[tuple[str, list[Equipment]]] = []
    for ownership in EquipmentOwnership.DISPLAY_ORDER:
        bucket = [item for item in items if item.ownership == ownership]
        if bucket:
            grouped.append((ownership, bucket))
    return grouped


def get_by_slug(slug: str, published_only: bool = True) -> Equipment | None:
    """依 slug 取單筆；published_only=False 供後台預覽使用。"""
    stmt = select(Equipment).where(Equipment.slug == slug)
    if published_only:
        stmt = stmt.where(Equipment.publish_status.in_(PublishStatus.PUBLIC))
    return db.session.execute(stmt).scalars().first()


def admin_list(
    ownership: str | None = None,
    status: str | None = None,
) -> list[Equipment]:
    """後台列表，可依歸屬與狀態篩選（含 draft 與 archived）。"""
    stmt = select(Equipment)
    if ownership:
        stmt = stmt.where(Equipment.ownership == ownership)
    if status:
        stmt = stmt.where(Equipment.publish_status == status)
    stmt = stmt.order_by(
        Equipment.ownership.asc(),
        Equipment.sort_order.asc(),
        Equipment.id.asc(),
    )
    return list(db.session.execute(stmt).scalars().all())


def count_by_status() -> dict[str, int]:
    """各發布狀態的筆數（Dashboard 用）。"""
    rows = db.session.execute(
        select(Equipment.publish_status, func.count(Equipment.id)).group_by(
            Equipment.publish_status
        )
    ).all()
    counts = {status: 0 for status in PublishStatus.ALL}
    for status, count in rows:
        counts[status] = count
    return counts


def needs_attention() -> list[dict]:
    """內容品質提醒（Dashboard 用，不阻擋草稿）。

    只回報「已發布卻缺關鍵資訊」的項目 —— 草稿本來就還在編輯中，
    列出來只會讓提醒區永遠有東西而失去意義。
    """
    issues: list[dict] = []
    for item in list_published():
        if item.photo_path and not (item.photo_alt_zh or "").strip():
            issues.append({"slug": item.slug, "issue": "已發布但照片缺 alt"})
        if item.ownership != EquipmentOwnership.LAB and not (
            (item.source_note or "").strip() or (item.source_url or "").strip()
        ):
            issues.append({"slug": item.slug, "issue": "非自有設備但缺出處"})
        if not (item.description_zh or item.description_en):
            issues.append({"slug": item.slug, "issue": "缺用途說明"})
    return issues
