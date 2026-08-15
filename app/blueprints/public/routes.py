# ============================================================
# NTUST SiPh Lab - Public Blueprint
#
# 上下游：
#   瀏覽器/爬蟲 -> public blueprint
#       -> repositories（people / research / settings）
#       -> SEOService（title/description/canonical/OG）
#       -> SchemaService（JSON-LD）
#       -> templates/public/*.html
#       -> 200 完整 HTML（SSR，ADR-001）
#   404 -> app 層 error handler 先查 redirects -> 301（AC-10）
#
# 檔案路徑：
#   app/blueprints/public/routes.py
#
# 建立日期：2026-08-14
# 最後重大修改：2026-08-14
# 版本：v1.0
#
# 模組定位與責任邊界：
#   SAI 附錄 A Route Matrix 中所有 Public 路由的實作：
#     /  /about  /members  /alumni  /people/<slug>
#     /research  /research/<slug>  /join
#     /sitemap.xml  /robots.txt  /healthz  /llms.txt
#
#   責任邊界（SAI §9.1，不得做的事）：
#     - 不得直接寫 select()（一律經 repositories）。
#     - 不得寫入資料庫。
#     - 不得在此組 SEO 字串（一律經 SEOService）。
#
# 輸入 -> 處理 -> 輸出 Pipeline：
#   GET /research/<slug>
#     -> research_repo.get_by_slug(slug, published_only=True)
#     -> 找不到 -> abort(404) -> 全域 handler 查 redirects -> 301 或 404
#     -> SEOService.build_research + SchemaService.graph
#     -> render_template -> 200 完整 HTML
#
# 主要 Function：
#   home / about / members / alumni / person_detail
#   research_index / research_detail / join
#   sitemap_xml / robots_txt / llms_txt / healthz
#   uploads(filename)  - local storage 的媒體服務路由
#
# 依賴套件：
#   flask, app.repositories, app.services
#
# 環境變數（透過 app.config）：
#   PUBLIC_BASE_URL, ROBOTS_POLICY, ENABLE_LLMS_TXT, STORAGE_BACKEND
#
# 資料庫使用方式：
#   只讀。任何 GET 都不得產生寫入（SiteSetting.get() 的首次
#   建立除外，見該方法說明）。
#
# Error Handling / Fallback：
#   - 內容不存在或非 published -> abort(404)，由全域 handler
#     先嘗試 redirect（SAI §9.2）。
#   - /healthz 在 DB 異常時回 503（SAI 附錄 A：200/503）。
#   - /llms.txt 在未啟用時回 404（避免提供空檔案）。
#
# 特殊機制（uploads 路由）：
#   只有 STORAGE_BACKEND=local 時才註冊 /uploads/<path>。
#   production 使用 GCS，媒體由 object storage 直接服務，
#   應用程式不應該也不需要代理檔案（SAI §21.4）。
#   若在 production 誤留此路由，等同讓 Cloud Run 承擔
#   靜態檔案流量，成本與延遲都不必要。
#
# 已知限制與禁止事項：
#   1. 禁止在公開頁顯示 draft/archived 內容（AC-08）。
#   2. 禁止在 canonical 中包含 query string（SAI §4.2）。
#   3. 禁止建立 /api/v1（SAI §9.5：v1 不需要 public API）。
#
# 維護契約：
#   新增公開頁面時，必須同時：
#     (a) 在 SEOService 新增對應的 build_* 函式
#     (b) 決定是否納入 sitemap_entries()
#     (c) 評估 structured data 適用性
#   這是 SAI §23.1 的 Agent 開發規則，缺一項就違反 §12 驗收。
#
# 驗證方式：
#   pytest tests/test_seo.py tests/test_people.py tests/test_research.py
# ============================================================

from __future__ import annotations

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    render_template,
    request,
    send_from_directory,
    url_for,
)

from app.models.mixins import OutputType
from app.repositories import people as people_repo
from app.repositories import research as research_repo
from app.repositories.settings import get_site_settings
from app.services.health_service import HealthService
from app.services.schema_service import SchemaService
from app.services.seo_service import SEOService
from app.utils.validators import validate_year

public_bp = Blueprint("public", __name__)


# ----------------------------------------------------------------------
# 首頁
# ----------------------------------------------------------------------
@public_bp.route("/")
def home():
    """首頁（SAI §5.1 的七個 section）。

    Featured outputs 若為空則以最新成果替代，
    避免產生空白區塊（見 research_repo.list_recent 的說明）。
    """
    settings = get_site_settings()

    featured = research_repo.list_featured(limit=6)
    if not featured:
        featured = research_repo.list_recent(limit=6)

    meta = SEOService.build_home()
    structured_data = SchemaService.graph(
        SchemaService.organization(),
        SchemaService.website(),
    )

    return render_template(
        "public/home.html",
        meta=meta,
        structured_data=structured_data,
        faculty=people_repo.get_faculty(),
        featured_outputs=featured,
        featured_people=people_repo.list_featured_people(limit=6),
        alumni_preview=people_repo.list_alumni_preview(limit=4),
        research_focus=settings.research_focus,
        lab_proof=settings.lab_proof,
    )


# ----------------------------------------------------------------------
# About
# ----------------------------------------------------------------------
@public_bp.route("/about")
def about():
    """關於研究室（SAI §4.1：教授、Lab、研究方向與方法概覽）。"""
    settings = get_site_settings()
    meta = SEOService.build_about()

    structured_data = SchemaService.graph(
        SchemaService.organization(),
        SchemaService.breadcrumb(meta.breadcrumbs, "關於研究室", meta.canonical),
    )

    return render_template(
        "public/about.html",
        meta=meta,
        structured_data=structured_data,
        faculty=people_repo.get_faculty(),
        research_focus=settings.research_focus,
    )


# ----------------------------------------------------------------------
# 成員
# ----------------------------------------------------------------------
@public_bp.route("/members")
def members():
    """在學研究成員列表（SAI §5.2）。"""
    meta = SEOService.build_members()
    member_list = people_repo.list_current_members()

    structured_data = SchemaService.graph(
        SchemaService.organization(),
        SchemaService.collection_page(
            name="研究成員",
            url=meta.canonical,
            description=meta.description,
            item_urls=[
                SEOService.absolute_url(url_for("public.person_detail", slug=p.slug))
                for p in member_list
            ],
        ),
        SchemaService.breadcrumb(meta.breadcrumbs, "研究成員", meta.canonical),
    )

    return render_template(
        "public/members.html",
        meta=meta,
        structured_data=structured_data,
        faculty=people_repo.get_faculty(),
        members=member_list,
    )


@public_bp.route("/alumni")
def alumni():
    """畢業生列表，依年度分組（SAI §5.4）。"""
    meta = SEOService.build_alumni()
    groups = people_repo.list_alumni_by_year()

    all_alumni = [person for _, group in groups for person in group]
    structured_data = SchemaService.graph(
        SchemaService.organization(),
        SchemaService.collection_page(
            name="畢業生",
            url=meta.canonical,
            description=meta.description,
            item_urls=[
                SEOService.absolute_url(url_for("public.person_detail", slug=p.slug))
                for p in all_alumni
            ],
        ),
        SchemaService.breadcrumb(meta.breadcrumbs, "畢業生", meta.canonical),
    )

    return render_template(
        "public/alumni.html",
        meta=meta,
        structured_data=structured_data,
        alumni_groups=groups,
    )


@public_bp.route("/people/<slug>")
def person_detail(slug: str):
    """人物詳細頁（SAI §5.2、§5.4；AC-05）。

    找不到時 abort(404)，由全域 handler 先查 redirects。
    """
    person = people_repo.get_by_slug(slug, published_only=True)
    if person is None:
        abort(404)

    meta = SEOService.build_person(person)

    structured_data = SchemaService.graph(
        SchemaService.organization(),
        SchemaService.person(person),
        SchemaService.breadcrumb(meta.breadcrumbs, person.name_zh, meta.canonical),
    )

    return render_template(
        "public/person_detail.html",
        meta=meta,
        structured_data=structured_data,
        person=person,
        related_outputs=person.related_outputs,
    )


# ----------------------------------------------------------------------
# 研究成果
# ----------------------------------------------------------------------
@public_bp.route("/research")
def research_index():
    """研究成果總覽與篩選（SAI §4.1、§5.3）。

    篩選參數（type / year）不改變 canonical（SAI §4.2）。
    """
    output_type = request.args.get("type") or None
    year = validate_year(request.args.get("year"))
    query = (request.args.get("q") or "").strip() or None

    outputs = research_repo.list_published(output_type=output_type, year=year, query=query)
    meta = SEOService.build_research_index()

    structured_data = SchemaService.graph(
        SchemaService.organization(),
        SchemaService.collection_page(
            name="研究成果",
            url=meta.canonical,
            description=meta.description,
            item_urls=[
                SEOService.absolute_url(url_for("public.research_detail", slug=o.slug))
                for o in outputs
            ],
        ),
        SchemaService.breadcrumb(meta.breadcrumbs, "研究成果", meta.canonical),
    )

    return render_template(
        "public/research_index.html",
        meta=meta,
        structured_data=structured_data,
        outputs=outputs,
        available_years=research_repo.available_years(),
        available_types=research_repo.available_types(),
        type_labels=OutputType.LABELS_ZH,
        active_type=output_type if output_type in OutputType.ALL else None,
        active_year=year,
        active_query=query,
    )


@public_bp.route("/research/<slug>")
def research_detail(slug: str):
    """研究成果詳細頁（SAI §5.3 —— SEO/GEO 最重要的內容單位）。"""
    output = research_repo.get_by_slug(slug, published_only=True)
    if output is None:
        abort(404)

    meta = SEOService.build_research(output)

    structured_data = SchemaService.graph(
        SchemaService.organization(),
        SchemaService.research_output(output),
        SchemaService.breadcrumb(meta.breadcrumbs, output.display_title, meta.canonical),
    )

    return render_template(
        "public/research_detail.html",
        meta=meta,
        structured_data=structured_data,
        output=output,
        related_people=output.public_lab_people,
    )


# ----------------------------------------------------------------------
# Join
# ----------------------------------------------------------------------
@public_bp.route("/join")
def join():
    """招募、聯絡與位置（SAI §4.1）。"""
    meta = SEOService.build_join()
    structured_data = SchemaService.graph(
        SchemaService.organization(),
        SchemaService.breadcrumb(meta.breadcrumbs, "加入我們", meta.canonical),
    )
    return render_template(
        "public/join.html",
        meta=meta,
        structured_data=structured_data,
        faculty=people_repo.get_faculty(),
    )


# ----------------------------------------------------------------------
# 機器可讀資源
# ----------------------------------------------------------------------
@public_bp.route("/sitemap.xml")
def sitemap_xml():
    """sitemap.xml（SAI §12.1；AC-08、AC-09）。

    只收 published canonical pages，lastmod 使用 updated_at。
    """
    entries = SEOService.sitemap_entries()
    xml = render_template("public/sitemap.xml", entries=entries)
    return Response(xml, mimetype="application/xml")


@public_bp.route("/robots.txt")
def robots_txt():
    """robots.txt（SAI §12.1）。

    規則：
      - 允許 public 內容。
      - 禁止 /admin/（管理後台不應被索引）。
      - 指示 sitemap 位置。
      - ROBOTS_POLICY=private 時全站 Disallow（staging 用）。

    為什麼 staging 需要全站 Disallow：
      run.app 的暫時網址若被索引，會與正式網域產生重複內容，
      且日後難以移除（SAI §24 學校 DNS 風險的連帶影響）。
    """
    policy = (current_app.config.get("ROBOTS_POLICY") or "public").lower()
    sitemap_url = SEOService.absolute_url(url_for("public.sitemap_xml"))

    body = render_template(
        "public/robots.txt",
        policy=policy,
        sitemap_url=sitemap_url,
        llms_enabled=_llms_enabled(),
        llms_url=SEOService.absolute_url(url_for("public.llms_txt")),
    )
    return Response(body, mimetype="text/plain")


def _llms_enabled() -> bool:
    """llms.txt 是否啟用。

    需要「設定檔開關」與「後台開關」同時為真：
      config.ENABLE_LLMS_TXT 讓部署者決定是否提供此實驗性功能；
      SiteSetting.llms_txt_enabled 讓管理者在後台開關。
    這是刻意的雙重控制 —— SAI §13.2 把它定義為實驗性相容層，
    不應該只靠單一開關就對外提供。
    """
    return bool(
        current_app.config.get("ENABLE_LLMS_TXT")
        and get_site_settings().llms_txt_enabled
    )


@public_bp.route("/llms.txt")
def llms_txt():
    """llms.txt（SAI §13.2 實驗性相容層）。

    重要：此檔案「不是」Google 排名的必要條件 [S5]。
    模板中已明確標註為 experimental，避免日後維護者誤解。

    未啟用時回 404 而非空檔案：空檔案會讓抓取者以為
    我們宣告了「沒有任何重要頁面」。
    """
    if not _llms_enabled():
        abort(404)

    settings = get_site_settings()
    body = render_template(
        "public/llms.txt",
        settings=settings,
        base_url=SEOService.base_url(),
        about_url=SEOService.absolute_url(url_for("public.about")),
        members_url=SEOService.absolute_url(url_for("public.members")),
        research_url=SEOService.absolute_url(url_for("public.research_index")),
        alumni_url=SEOService.absolute_url(url_for("public.alumni")),
        join_url=SEOService.absolute_url(url_for("public.join")),
    )
    return Response(body, mimetype="text/plain")


@public_bp.route("/healthz")
def healthz():
    """健康檢查（SAI §19、附錄 A：200/503）。

    回應內容刻意極簡，不含版本或環境資訊。
    """
    ok, status = HealthService.liveness()
    return Response(
        status + "\n",
        status=200 if ok else 503,
        mimetype="text/plain",
    )


# ----------------------------------------------------------------------
# 本機媒體服務
# ----------------------------------------------------------------------
@public_bp.route("/uploads/<path:filename>")
def uploads(filename: str):
    """服務本機 uploads 目錄的媒體檔（僅 STORAGE_BACKEND=local）。

    production（GCS）不應走到這裡：媒體由 object storage 直接服務。
    因此在非 local backend 時直接 404，避免無意義的代理路徑
    （見檔頭「特殊機制（uploads 路由）」）。

    安全性：
      send_from_directory 會拒絕逃出根目錄的路徑，
      加上 storage.base.validate_object_key 已在寫入端限制字元集，
      形成雙重防護。
    """
    if (current_app.config.get("STORAGE_BACKEND") or "local").lower() != "local":
        abort(404)

    upload_dir = current_app.config.get("UPLOAD_DIR")
    if not upload_dir:
        abort(404)

    # max_age：媒體檔名含 UUID，內容不變，可長期快取（SAI §18）。
    return send_from_directory(upload_dir, filename, max_age=60 * 60 * 24 * 30)
