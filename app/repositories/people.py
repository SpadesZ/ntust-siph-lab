# ============================================================
# NTUST SiPh Lab - People Repository
#
# 上下游：
#   blueprints/public/routes.py -> people repository -> Person -> Template
#   blueprints/admin/routes.py  -> people repository -> Admin 列表
#   services/seo_service.py（sitemap）-> published_people()
#   services/person_service.py -> 寫入前的查詢
#
# 檔案路徑：
#   app/repositories/people.py
#
# 建立日期：2026-08-14
# 最後重大修改：2026-08-14
# 版本：v1.0
#
# 模組定位與責任邊界：
#   人物查詢的唯一邊界（SAI §9.4「DB-portable query boundary」）。
#   所有 /members、/alumni、/people/<slug> 與 Admin 列表的查詢
#   都必須經過此模組，理由有二：
#     1. 保證「只有 published 才出現在前台」這條規則不會在某個
#        新頁面被遺漏（SAI §7.4、AC-08）。
#     2. 保證查詢語法可攜到 PostgreSQL（SAI §10.2）。
#
#   責任邊界（不得做的事）：
#     - 不得寫入或 commit。
#     - 不得寫 AuditLog。
#     - 不得 render 或組 HTML。
#
# 輸入 -> 處理 -> 輸出 Pipeline：
#   輸入：status / publish_status / 搜尋字串 / 年度
#   處理：SQLAlchemy select + where + order_by
#   輸出：Person list 或單一 Person / 統計 dict
#
# 主要 Function：
#   get_by_slug(slug, published_only)
#   list_current_members()      - /members
#   list_alumni_by_year()       - /alumni（依畢業年度分組）
#   get_faculty()               - 教授（首頁 / About）
#   list_featured_people()      - 首頁 Current members 區塊
#   published_people()          - sitemap 用
#   admin_list(...)             - Admin 列表（含 draft/archived）
#   count_by_status()           - Dashboard Content status
#   needs_attention()           - Dashboard 內容品質提醒
#
# 依賴套件：
#   sqlalchemy, app.extensions.db, app.models
#
# 環境變數：無。
#
# 資料庫使用方式：
#   利用 SAI §8.9 定義的 composite index
#   (status, publish_status, sort_order) —— 因此列表查詢的
#   where/order_by 順序刻意與索引欄位順序對齊。
#
# Error Handling / Fallback：
#   查無資料一律回空 list 或 None，不拋例外。
#   404 的決定權在 blueprint（因為要先查 redirects）。
#
# 特殊機制（N+1 防治）：
#   涉及 related outputs 的查詢使用 selectinload 預載關聯，
#   避免 /members 每張卡各發一次查詢。selectinload 在
#   SQLite 與 PostgreSQL 行為一致（產生 IN 查詢），
#   符合可攜契約。
#
# 已知限制與禁止事項：
#   1. 禁止在此加入 raw SQL 或 SQLite-only 函式
#      （例如 GROUP_CONCAT、strftime）。PostgreSQL 沒有相同語意。
#   2. 禁止在前台查詢中省略 publish_status 過濾。
#   3. 搜尋使用 ILIKE 語意；在 SQLite 上 LIKE 對 ASCII 預設
#      不分大小寫，對中文則是精確比對 —— 兩種 DB 的中文行為
#      一致，故可接受。全文檢索不在 v1 範圍。
#
# 維護契約：
#   新增前台人物列表時，必須使用本模組既有函式或新增於此，
#   不得在 blueprint 直接寫 select()。否則 draft 內容外洩的
#   風險會隨頁面數量線性增加。
#
# 驗證方式：
#   pytest tests/test_people.py
#   pytest tests/test_db_portability.py
# ============================================================

from __future__ import annotations

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models.mixins import PersonStatus, PublishStatus
from app.models.person import Person
from app.models.research_output import ResearchOutputPerson


def _with_outputs(stmt):
    """為查詢加上關聯預載，避免 N+1（見檔頭「特殊機制」）。

    兩層 selectinload：先載入 Person.output_links（關聯物件），
    再從關聯物件載入 research_output 本體。少了第二層，
    Person.related_outputs 仍會對每個關聯各發一次查詢。
    """
    return stmt.options(
        selectinload(Person.output_links).selectinload(ResearchOutputPerson.research_output)
    )


def get_by_slug(slug: str, published_only: bool = True) -> Person | None:
    """依 slug 取得人物。

    Args:
        published_only: True 時只回傳已發布者（前台使用）。
                        Admin 預覽 draft 時傳 False。

    Returns:
        Person 或 None。None 時呼叫端應先查 redirects 再決定 404。
    """
    if not slug:
        return None

    stmt = select(Person).where(Person.slug == slug)
    if published_only:
        stmt = stmt.where(Person.publish_status == PublishStatus.PUBLISHED)

    return db.session.scalar(_with_outputs(stmt))


def get_faculty(published_only: bool = True) -> Person | None:
    """取得教授（status=faculty）。

    為什麼回單一物件：
      本案為單一 PI 研究室。若未來有多位教師，
      應改為 list 並更新 About/首頁模板 —— 屆時需新增 ADR。
      目前回傳 sort_order 最小者，行為明確不含隱性假設。
    """
    stmt = select(Person).where(Person.status == PersonStatus.FACULTY)
    if published_only:
        stmt = stmt.where(Person.publish_status == PublishStatus.PUBLISHED)
    stmt = stmt.order_by(Person.sort_order.asc(), Person.id.asc())
    return db.session.scalar(_with_outputs(stmt))


def list_current_members(published_only: bool = True) -> list[Person]:
    """在學研究成員列表（/members）。

    排序：sort_order -> entry_year（新到舊）-> 姓名。
    為什麼以 sort_order 為主：SAI §7.3 要求 Admin 可自訂排序，
    管理者的顯式順序必須優先於任何自動規則。
    """
    stmt = select(Person).where(Person.status == PersonStatus.CURRENT)
    if published_only:
        stmt = stmt.where(Person.publish_status == PublishStatus.PUBLISHED)

    stmt = stmt.order_by(
        Person.sort_order.asc(),
        Person.entry_year.desc().nullslast(),
        Person.name_zh.asc(),
    )
    return list(db.session.scalars(_with_outputs(stmt)).unique().all())


def list_alumni(published_only: bool = True) -> list[Person]:
    """畢業生列表（新到舊）。"""
    stmt = select(Person).where(Person.status == PersonStatus.ALUMNI)
    if published_only:
        stmt = stmt.where(Person.publish_status == PublishStatus.PUBLISHED)

    stmt = stmt.order_by(
        Person.graduation_year.desc().nullslast(),
        Person.sort_order.asc(),
        Person.name_zh.asc(),
    )
    return list(db.session.scalars(_with_outputs(stmt)).unique().all())


def list_alumni_by_year(published_only: bool = True) -> list[tuple[int | None, list[Person]]]:
    """畢業生依年度分組（/alumni）。

    回傳 [(year, [Person, ...]), ...]，年度由新到舊；
    未填年度者歸在最後的 None 群組。

    為什麼在 Python 分組而不用 SQL GROUP BY：
      SQL 分組後仍需要每組的完整 Person 物件，等於要做兩次查詢
      或用 DB-specific 的陣列聚合函式（PostgreSQL 有 array_agg，
      SQLite 沒有）。資料量級是數十筆，in-memory 分組成本可忽略，
      且完全可攜（SAI §10.2）。
    """
    grouped: dict[int | None, list[Person]] = {}
    for person in list_alumni(published_only=published_only):
        grouped.setdefault(person.graduation_year, []).append(person)

    # 有年度者由新到舊，None 排最後。
    years_with_value = sorted((y for y in grouped if y is not None), reverse=True)
    result: list[tuple[int | None, list[Person]]] = [(y, grouped[y]) for y in years_with_value]
    if None in grouped:
        result.append((None, grouped[None]))
    return result


def list_featured_people(limit: int = 6) -> list[Person]:
    """首頁 Current members 區塊（SAI §5.1 順序 04：教授 + 在學學生精簡卡）。"""
    stmt = (
        select(Person)
        .where(
            Person.publish_status == PublishStatus.PUBLISHED,
            Person.status.in_((PersonStatus.FACULTY, PersonStatus.CURRENT)),
        )
        .order_by(
            # 教授優先：faculty 在字母序上剛好排在 current 之前，
            # 但依賴字母序太隱晦，故用顯式 CASE 表達意圖。
            # CASE 在 SQLite 與 PostgreSQL 語法一致，符合可攜契約。
            case((Person.status == PersonStatus.FACULTY, 0), else_=1).asc(),
            Person.sort_order.asc(),
            Person.name_zh.asc(),
        )
        .limit(limit)
    )
    return list(db.session.scalars(stmt).unique().all())


def list_alumni_preview(limit: int = 4) -> list[Person]:
    """首頁 Alumni preview 區塊（SAI §5.1 順序 06）。"""
    stmt = (
        select(Person)
        .where(
            Person.publish_status == PublishStatus.PUBLISHED,
            Person.status == PersonStatus.ALUMNI,
        )
        .order_by(Person.graduation_year.desc().nullslast(), Person.name_zh.asc())
        .limit(limit)
    )
    return list(db.session.scalars(stmt).unique().all())


def published_people() -> list[Person]:
    """所有已發布人物（sitemap 用，SAI §12.1）。

    只回傳 published —— draft 與 archived 不得進 sitemap（AC-08）。
    """
    stmt = (
        select(Person)
        .where(Person.publish_status == PublishStatus.PUBLISHED)
        .order_by(Person.updated_at.desc())
    )
    return list(db.session.scalars(stmt).all())


def admin_list(
    status: str | None = None,
    publish_status: str | None = None,
    query: str | None = None,
) -> list[Person]:
    """Admin 人物列表（含 draft/archived）。

    Args:
        status: faculty/current/alumni 篩選。
        publish_status: draft/published/archived 篩選。
        query: 姓名或 slug 模糊搜尋。
    """
    stmt = select(Person)

    if status in PersonStatus.ALL:
        stmt = stmt.where(Person.status == status)
    if publish_status in PublishStatus.ALL:
        stmt = stmt.where(Person.publish_status == publish_status)

    if query:
        pattern = f"%{query.strip()}%"
        stmt = stmt.where(
            or_(
                Person.name_zh.ilike(pattern),
                Person.name_en.ilike(pattern),
                Person.slug.ilike(pattern),
            )
        )

    stmt = stmt.order_by(
        Person.status.asc(),
        Person.sort_order.asc(),
        Person.name_zh.asc(),
    )
    return list(db.session.scalars(stmt).all())


def count_by_status() -> dict[str, dict[str, int]]:
    """Dashboard 的 Content status 統計（SAI §7.2）。

    Returns:
        {"current": {"published": 4, "draft": 0, "archived": 0}, ...}
    """
    rows = db.session.execute(
        select(Person.status, Person.publish_status, func.count(Person.id)).group_by(
            Person.status, Person.publish_status
        )
    ).all()

    result: dict[str, dict[str, int]] = {
        s: {p: 0 for p in PublishStatus.ALL} for s in PersonStatus.ALL
    }
    for status, publish_status, count in rows:
        if status in result and publish_status in result[status]:
            result[status][publish_status] = count
    return result


def needs_attention() -> list[dict]:
    """Dashboard「Needs attention」的人物項目（SAI §7.2）。

    檢查項目：
      - 有照片但缺 alt（影響 a11y 與 AC-12）
      - 已發布但缺研究焦點（legacy_pending_detail 者仍列出，
        這正是 2026-08-14 管理者裁示要求的持續提醒）
      - 已發布但缺英文姓名（影響英文 SEO 與 Person JSON-LD）

    為什麼回 list[dict] 而非 model：
      Dashboard 要顯示的是「問題描述 + 連結」，不是完整實體。
      回傳輕量 dict 可避免 template 需要再判斷問題種類。
    """
    issues: list[dict] = []

    people = db.session.scalars(
        select(Person).order_by(Person.status.asc(), Person.sort_order.asc())
    ).all()

    for person in people:
        if person.photo_path and not person.photo_alt_zh:
            issues.append(
                {
                    "entity": "person",
                    "id": person.id,
                    "slug": person.slug,
                    "name": person.name_zh,
                    "issue": "有照片但缺 alt 文字",
                    "severity": "blocking",
                }
            )

        if person.publish_status == PublishStatus.PUBLISHED:
            if not (person.research_focus_zh or person.research_focus_en):
                issues.append(
                    {
                        "entity": "person",
                        "id": person.id,
                        "slug": person.slug,
                        "name": person.name_zh,
                        "issue": (
                            "已發布但缺研究焦點（母站遷入待補）"
                            if person.legacy_pending_detail
                            else "已發布但缺研究焦點"
                        ),
                        "severity": "warning",
                    }
                )
            if not person.name_en:
                issues.append(
                    {
                        "entity": "person",
                        "id": person.id,
                        "slug": person.slug,
                        "name": person.name_zh,
                        "issue": "缺英文姓名",
                        "severity": "info",
                    }
                )

    return issues


def count_legacy_pending() -> int:
    """待補齊 legacy 欄位的人物數（供 Dashboard 與驗收腳本使用）。"""
    return db.session.scalar(
        select(func.count(Person.id)).where(Person.legacy_pending_detail.is_(True))
    ) or 0
