# ============================================================
# NTUST SiPh Lab - Research Output Repository
#
# 上下游：
#   blueprints/public/routes.py -> research repository -> Template
#   blueprints/admin/routes.py  -> research repository -> Admin 列表
#   services/seo_service.py（sitemap）-> published_outputs()
#   services/research_service.py -> 寫入前的查詢
#
# 檔案路徑：
#   app/repositories/research.py
#
# 建立日期：2026-08-14
# 最後重大修改：2026-08-14
# 版本：v1.0
#
# 模組定位與責任邊界：
#   研究成果查詢的唯一邊界（SAI §9.4）。負責保證：
#     1. draft/archived 成果絕不出現在前台與 sitemap（AC-08、AC-09）。
#     2. 查詢語法可攜到 PostgreSQL（SAI §10.2）。
#
#   責任邊界（不得做的事）：
#     - 不得寫入或 commit；不得寫 AuditLog；不得 render HTML。
#
# 輸入 -> 處理 -> 輸出 Pipeline：
#   輸入：type / year / 關鍵字 / featured 條件
#   處理：SQLAlchemy select + where + order_by + selectinload
#   輸出：ResearchOutput list / 單一物件 / 統計 dict
#
# 主要 Function：
#   get_by_slug(slug, published_only)
#   list_published(output_type, year, query)  - /research 篩選
#   list_featured(limit)                      - 首頁 Featured outputs
#   available_years() / available_types()     - 篩選器選項
#   published_outputs()                       - sitemap 用
#   admin_list(...)                           - Admin 列表
#   count_by_status() / needs_attention()     - Dashboard
#
# 依賴套件：
#   sqlalchemy, app.extensions.db, app.models
#
# 環境變數：無。
#
# 資料庫使用方式：
#   對齊 SAI §8.9 的 index(publish_status, year, output_type)
#   與 index(is_featured)。
#
# Error Handling / Fallback：
#   查無資料回空 list 或 None，不拋例外。
#   篩選參數不在允許值內時「忽略該篩選」而非回空結果 ——
#   為什麼：使用者手改 query string 時，顯示全部比顯示空白
#   更符合預期，也避免產生可被索引的空頁面（SAI §14.3
#   publish gate：page is empty/thin 應被拒絕）。
#
# 特殊機制（N+1 防治）：
#   列表查詢預載 person_links -> person，讓成果卡可直接顯示
#   Lab 作者而不觸發額外查詢。
#
# 已知限制與禁止事項：
#   1. 禁止 raw SQL 與 SQLite-only 函式。
#   2. 禁止在前台查詢省略 publish_status 過濾。
#   3. 關鍵字搜尋為 LIKE 比對，非全文檢索；
#      v1 資料量級（數十筆）不需要 FTS，且 FTS 語法在
#      SQLite（FTS5）與 PostgreSQL（tsvector）完全不同，
#      導入會直接違反可攜契約。
#
# 維護契約：
#   新增前台成果列表時必須使用本模組，不得在 blueprint 直接
#   寫 select()，理由同 people repository。
#
# 驗證方式：
#   pytest tests/test_research.py
#   pytest tests/test_seo.py
# ============================================================

from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models.mixins import OutputType, PublishStatus
from app.models.research_output import ResearchOutput, ResearchOutputPerson


def _with_people(stmt):
    """預載 Lab 作者關聯，避免列表頁 N+1。"""
    return stmt.options(
        selectinload(ResearchOutput.person_links).selectinload(ResearchOutputPerson.person)
    )


def get_by_slug(slug: str, published_only: bool = True) -> ResearchOutput | None:
    """依 slug 取得成果。找不到回 None（由 blueprint 決定 301/404）。"""
    if not slug:
        return None

    stmt = select(ResearchOutput).where(ResearchOutput.slug == slug)
    if published_only:
        stmt = stmt.where(ResearchOutput.publish_status == PublishStatus.PUBLISHED)

    return db.session.scalar(_with_people(stmt))


def list_published(
    output_type: str | None = None,
    year: int | None = None,
    query: str | None = None,
) -> list[ResearchOutput]:
    """公開研究成果列表（/research）。

    排序：年份新到舊 -> sort_order -> id。
    為什麼年份優先於 sort_order（與人物相反）：
      研究成果的時序是讀者最主要的心智模型（SAI §5.3 每筆成果
      都必須有年份），而人物列表的順序則是研究室的組織決定。
    """
    stmt = select(ResearchOutput).where(
        ResearchOutput.publish_status == PublishStatus.PUBLISHED
    )

    # 不合法的篩選值一律忽略（見檔頭 Fallback 說明）。
    if output_type in OutputType.ALL:
        stmt = stmt.where(ResearchOutput.output_type == output_type)
    if year:
        stmt = stmt.where(ResearchOutput.year == year)

    if query:
        pattern = f"%{query.strip()}%"
        stmt = stmt.where(
            or_(
                ResearchOutput.title_zh.ilike(pattern),
                ResearchOutput.title_en.ilike(pattern),
                ResearchOutput.summary_zh.ilike(pattern),
                ResearchOutput.summary_en.ilike(pattern),
                ResearchOutput.keywords_json.ilike(pattern),
                ResearchOutput.venue.ilike(pattern),
            )
        )

    stmt = stmt.order_by(
        ResearchOutput.year.desc(),
        ResearchOutput.sort_order.asc(),
        ResearchOutput.id.desc(),
    )
    return list(db.session.scalars(_with_people(stmt)).unique().all())


def list_featured(limit: int = 6) -> list[ResearchOutput]:
    """首頁 Featured outputs（SAI §5.1 順序 03：3-6 筆）。

    只取 published 且 is_featured —— SAI §15.2 明定
    「published 才能 featured」。
    """
    stmt = (
        select(ResearchOutput)
        .where(
            ResearchOutput.publish_status == PublishStatus.PUBLISHED,
            ResearchOutput.is_featured.is_(True),
        )
        .order_by(
            ResearchOutput.sort_order.asc(),
            ResearchOutput.year.desc(),
            ResearchOutput.id.desc(),
        )
        .limit(limit)
    )
    return list(db.session.scalars(_with_people(stmt)).unique().all())


def list_recent(limit: int = 6) -> list[ResearchOutput]:
    """最新已發布成果。

    用途：首頁在「尚無 featured 成果」時的替代內容。
    為什麼需要：全新網站的 featured 清單一定是空的，
    若首頁因此出現空白區塊，就違反 SAI §14.3 的
    「page is empty/thin 應拒絕發布」精神。
    """
    stmt = (
        select(ResearchOutput)
        .where(ResearchOutput.publish_status == PublishStatus.PUBLISHED)
        .order_by(ResearchOutput.year.desc(), ResearchOutput.updated_at.desc())
        .limit(limit)
    )
    return list(db.session.scalars(_with_people(stmt)).unique().all())


def available_years() -> list[int]:
    """已發布成果涵蓋的年份（新到舊），供 /research 篩選器。"""
    rows = db.session.scalars(
        select(ResearchOutput.year)
        .where(ResearchOutput.publish_status == PublishStatus.PUBLISHED)
        .distinct()
        .order_by(ResearchOutput.year.desc())
    ).all()
    return [year for year in rows if year is not None]


def available_types() -> list[str]:
    """已發布成果實際存在的型別，供 /research 篩選器。

    只列出「真的有資料」的型別，避免使用者點了空篩選 ——
    空結果頁對 SEO 與使用者都是負擔。
    """
    rows = db.session.scalars(
        select(ResearchOutput.output_type)
        .where(ResearchOutput.publish_status == PublishStatus.PUBLISHED)
        .distinct()
    ).all()
    # 依 OutputType.ALL 的宣告順序回傳，讓篩選器順序穩定。
    present = set(rows)
    return [t for t in OutputType.ALL if t in present]


def published_outputs() -> list[ResearchOutput]:
    """所有已發布成果（sitemap 用，AC-09）。"""
    stmt = (
        select(ResearchOutput)
        .where(ResearchOutput.publish_status == PublishStatus.PUBLISHED)
        .order_by(ResearchOutput.updated_at.desc())
    )
    return list(db.session.scalars(stmt).all())


def admin_list(
    output_type: str | None = None,
    year: int | None = None,
    publish_status: str | None = None,
    query: str | None = None,
) -> list[ResearchOutput]:
    """Admin 成果列表（含 draft/archived，SAI §7.3 篩選需求）。"""
    stmt = select(ResearchOutput)

    if output_type in OutputType.ALL:
        stmt = stmt.where(ResearchOutput.output_type == output_type)
    if year:
        stmt = stmt.where(ResearchOutput.year == year)
    if publish_status in PublishStatus.ALL:
        stmt = stmt.where(ResearchOutput.publish_status == publish_status)

    if query:
        pattern = f"%{query.strip()}%"
        stmt = stmt.where(
            or_(
                ResearchOutput.title_zh.ilike(pattern),
                ResearchOutput.title_en.ilike(pattern),
                ResearchOutput.slug.ilike(pattern),
            )
        )

    stmt = stmt.order_by(
        ResearchOutput.year.desc(),
        ResearchOutput.sort_order.asc(),
        ResearchOutput.id.desc(),
    )
    return list(db.session.scalars(_with_people(stmt)).unique().all())


def admin_available_years() -> list[int]:
    """Admin 篩選用年份（含未發布）。"""
    rows = db.session.scalars(
        select(ResearchOutput.year).distinct().order_by(ResearchOutput.year.desc())
    ).all()
    return [year for year in rows if year is not None]


def count_by_status() -> dict[str, int]:
    """Dashboard 的成果狀態統計（SAI §7.2）。"""
    rows = db.session.execute(
        select(ResearchOutput.publish_status, func.count(ResearchOutput.id)).group_by(
            ResearchOutput.publish_status
        )
    ).all()

    result = {status: 0 for status in PublishStatus.ALL}
    for publish_status, count in rows:
        if publish_status in result:
            result[publish_status] = count
    return result


def needs_attention() -> list[dict]:
    """Dashboard「Needs attention」的成果項目（SAI §7.2）。

    檢查項目對應 SAI §7.2 所列：缺照片 alt、缺英文標題、
    缺 meta description，再加上本案特有的「缺方法/結果」——
    後者直接影響 GEO contract（SAI §13.1 Answerable structure）。
    """
    issues: list[dict] = []

    outputs = db.session.scalars(
        select(ResearchOutput).order_by(ResearchOutput.year.desc())
    ).all()

    for output in outputs:
        if output.hero_image_path and not output.hero_image_alt_zh:
            issues.append(
                {
                    "entity": "research",
                    "id": output.id,
                    "slug": output.slug,
                    "name": output.display_title,
                    "issue": "有主圖但缺 alt 文字",
                    "severity": "blocking",
                }
            )

        if output.publish_status != PublishStatus.PUBLISHED:
            continue

        if not output.title_en:
            issues.append(
                {
                    "entity": "research",
                    "id": output.id,
                    "slug": output.slug,
                    "name": output.display_title,
                    "issue": "缺英文標題",
                    "severity": "info",
                }
            )
        if not output.has_research_body:
            issues.append(
                {
                    "entity": "research",
                    "id": output.id,
                    "slug": output.slug,
                    "name": output.display_title,
                    "issue": "缺 Method 或 Key results（影響 GEO 可答性）",
                    "severity": "warning",
                }
            )
        if not (output.seo_description_zh or output.summary_zh or output.summary_en):
            issues.append(
                {
                    "entity": "research",
                    "id": output.id,
                    "slug": output.slug,
                    "name": output.display_title,
                    "issue": "缺 meta description 來源（無 summary 也無 override）",
                    "severity": "warning",
                }
            )

    return issues
