# ============================================================
# NTUST SiPh Lab - JSON-LD Structured Data Service
#
# 上下游：
#   blueprints/public/routes.py -> SchemaService.* -> dict
#       -> templates/public/base.html -> <script type="application/ld+json">
#   SchemaService -> SiteSetting（Organization / WebSite）
#   SchemaService -> Person / ResearchOutput（實體 JSON-LD）
#   SchemaService -> SEOService.absolute_url（canonical @id）
#
# 檔案路徑：
#   app/services/schema_service.py
#
# 建立日期：2026-08-14
# 最後重大修改：2026-08-14
# 版本：v1.0
#
# 模組定位與責任邊界：
#   實作 SAI §12.2 structured data 規格。Google 建議在可行時
#   使用 JSON-LD [S6]，但 structured data「必須代表頁面主內容、
#   不能誤導或描述使用者看不到的內容」[S7]。
#
#   本模組最重要的設計原則，也是 AC-17 的直接對應：
#     **只輸出頁面上真的看得到的值。**
#   因此每個欄位在加入前都會檢查來源資料是否存在且已公開；
#   任何「猜測、推導、預設值」都不得進入輸出。
#
#   責任邊界（不得做的事）：
#     - 不得輸出 aggregateRating、citation count 等本案沒有
#       事實依據的欄位（SAI §6.3 禁止虛構數據）。
#     - 不得對非學術成果輸出 ScholarlyArticle [S14]。
#     - 不得輸出未經確認可公開的個資（例如 alumni 就業資訊）。
#
# 輸入 -> 處理 -> 輸出 Pipeline：
#   輸入：SiteSetting / Person / ResearchOutput
#   處理：欄位存在性檢查 -> 型別選擇 -> dict 組裝 -> 清除空值
#   輸出：可直接 json.dumps 的 dict（或 list[dict]）
#
# 主要 Function：
#   organization()          - Organization（全站）
#   website()               - WebSite（首頁）
#   person(p)               - Person [S13]
#   research_output(o)      - ScholarlyArticle 或 CreativeWork
#   breadcrumb(items)       - BreadcrumbList
#   collection_page(...)    - CollectionPage（列表頁）
#   graph(*blocks)          - 合併為單一 @graph
#
# 依賴套件：
#   flask（url_for）、app.services.seo_service、app.models
#
# 環境變數：
#   間接使用 PUBLIC_BASE_URL（透過 SEOService.absolute_url）。
#
# 資料庫使用方式：只讀，不寫入。
#
# Error Handling / Fallback：
#   缺欄位時「省略該 property」而非輸出 null 或空字串。
#   為什麼：schema.org 的驗證工具會把 "" 視為有值但無效，
#   產生警告；省略則完全合法。_compact() 負責這件事。
#
# 特殊機制（@graph 合併）：
#   同一頁可能同時需要 Organization、WebSite、Person 與
#   BreadcrumbList。輸出成多個 <script> 標籤是合法的，
#   但用單一 @graph 可讓實體之間以 @id 互相參照
#   （例如 Person.affiliation 指向 Organization 的 @id），
#   這正是 SAI §13.1「Cross-entity linking」與
#   「Entity clarity」要的效果。
#
# 已知限制與禁止事項：
#   1. 不使用 ResearchOrganization 型別。SAI §12.2 [S12] 指出
#      它在 schema.org 仍屬較新型別，建議必要時保守使用
#      Organization —— 本案採保守方案。
#   2. 禁止把 Rich Results Test 通過等同於一定會顯示 rich result
#      （SAI §13.3）。
#   3. alumniOf 只在有事實依據時輸出（人物確實畢業於本校）。
#
# 維護契約：
#   新增任何 property 前，必須先確認「該值是否出現在頁面可見
#   內容中」。若否，即使 schema.org 允許也不得輸出 ——
#   這會直接造成 AC-17 失敗。
#
# 驗證方式：
#   pytest tests/test_schema.py
#   （另建議以 Google Rich Results Test 人工複驗）
# ============================================================

from __future__ import annotations

from flask import url_for

from app.models.mixins import PersonStatus
from app.models.person import Person
from app.models.research_output import ResearchOutput
from app.models.site_setting import SiteSetting
from app.services.seo_service import SEOService
from app.storage import get_storage

#: schema.org context。
_CONTEXT = "https://schema.org"


def _compact(data: dict) -> dict:
    """移除值為 None、空字串、空 list/dict 的鍵。

    為什麼不保留空值：見檔頭 Error Handling。
    注意：False 與 0 會被保留（它們是有意義的值）。
    """
    result = {}
    for key, value in data.items():
        if value is None:
            continue
        if isinstance(value, (str, list, dict, tuple)) and len(value) == 0:
            continue
        result[key] = value
    return result


def _image_url(object_key: str | None) -> str | None:
    """storage key -> 絕對 URL；失敗回 None。"""
    if not object_key:
        return None
    try:
        return SEOService.absolute_url(get_storage().public_url(object_key))
    except Exception:  # noqa: BLE001 - JSON-LD 缺圖不應讓頁面失敗
        return None


class SchemaService:
    """產生 JSON-LD structured data（SAI §12.2）。"""

    # ------------------------------------------------------------------
    # 全站實體
    # ------------------------------------------------------------------
    @staticmethod
    def organization_id() -> str:
        """Organization 的穩定 @id。

        使用 "<base>/#organization" 這種 fragment 形式，
        讓其他實體（Person.affiliation）能以 @id 參照同一個組織，
        而不是重複輸出整個組織物件。
        """
        return f"{SEOService.base_url()}/#organization"

    @staticmethod
    def organization() -> dict:
        """Organization JSON-LD（SAI §12.2）。

        保守使用 Organization 而非 ResearchOrganization，
        理由見檔頭「已知限制 1」。

        只輸出頁面可見的資訊：
          name / url 一定可見（header、footer）；
          logo 只在有上傳時輸出；
          parentOrganization 只在有填學校時輸出（About 頁可見）；
          sameAs 來自 footer 實際存在的連結。
        """
        site = SiteSetting.get()

        data = {
            "@type": "Organization",
            "@id": SchemaService.organization_id(),
            "name": site.lab_name_zh,
            "url": SEOService.base_url() or None,
        }

        if site.lab_name_en and site.lab_name_en != site.lab_name_zh:
            data["alternateName"] = site.lab_name_en

        logo = _image_url(site.logo_path)
        if logo:
            data["logo"] = logo

        # parentOrganization：學校。只有在 About 頁確實顯示時才輸出。
        if site.university_zh:
            parent = {"@type": "CollegeOrUniversity", "name": site.university_zh}
            if site.university_en:
                parent["alternateName"] = site.university_en
            data["parentOrganization"] = parent

        if site.department_zh:
            data["department"] = _compact(
                {
                    "@type": "Organization",
                    "name": site.department_zh,
                    "alternateName": site.department_en,
                }
            )

        if site.contact_email:
            data["email"] = site.contact_email

        if site.address_zh:
            # 使用 PostalAddress 的 streetAddress 保守表示；
            # 不拆解成 city/postalCode，因為那些值母站沒有提供，
            # 拆解就等於猜測（SAI §2.3）。
            data["address"] = {
                "@type": "PostalAddress",
                "streetAddress": site.address_zh,
                "addressCountry": "TW",
            }

        sameas = site.sameas_urls
        if sameas:
            data["sameAs"] = sameas

        return _compact(data)

    @staticmethod
    def website() -> dict:
        """WebSite JSON-LD（首頁，SAI §12.2）。

        刻意不輸出 potentialAction/SearchAction：
          本站沒有站內搜尋功能，宣告 SearchAction 會描述
          使用者實際上做不到的行為，違反 [S7]。
        """
        site = SiteSetting.get()
        return _compact(
            {
                "@type": "WebSite",
                "@id": f"{SEOService.base_url()}/#website",
                "name": site.lab_name_zh,
                "alternateName": site.lab_name_en
                if site.lab_name_en != site.lab_name_zh
                else None,
                "url": SEOService.base_url() or None,
                "inLanguage": "zh-Hant-TW",
                "publisher": {"@id": SchemaService.organization_id()},
            }
        )

    # ------------------------------------------------------------------
    # Person
    # ------------------------------------------------------------------
    @staticmethod
    def person(person: Person) -> dict:
        """Person JSON-LD [S13]。

        嚴格規則（AC-17）：
          - name 一定可見。
          - image 只在有照片時輸出。
          - jobTitle 來自頁面顯示的職稱/學位。
          - memberOf 指向 Organization @id（頁面上有 Lab 名稱與連結）。
          - alumniOf 只在「畢業生且有學校資訊」時輸出。
          - sameAs 只包含頁面上實際可點擊的外部連結。
          - email 只在 email_public 有值時輸出（該值本來就會顯示）。
          - 就業資訊只在 destination_public=True 時輸出。
        """
        site = SiteSetting.get()

        data = {
            "@type": "Person",
            "@id": SEOService.absolute_url(url_for("public.person_detail", slug=person.slug))
            + "#person",
            "name": person.name_zh,
            "url": SEOService.absolute_url(
                url_for("public.person_detail", slug=person.slug)
            ),
        }

        if person.name_en:
            data["alternateName"] = person.name_en

        image = _image_url(person.photo_path)
        if image:
            data["image"] = image

        # jobTitle：教授用職稱，學生用學位/身分。頁面首屏都會顯示。
        job_title = person.title_zh or person.degree
        if job_title:
            data["jobTitle"] = job_title

        # 描述：使用研究焦點（頁面可見）。
        if person.research_focus_zh or person.research_focus_en:
            data["description"] = SEOService.truncate_description(
                person.research_focus_zh or person.research_focus_en
            )

        # memberOf：所有 Lab 成員頁都顯示所屬研究室。
        data["memberOf"] = {"@id": SchemaService.organization_id()}

        # alumniOf：只有畢業生，且必須有學校名稱這個事實依據。
        if person.status == PersonStatus.ALUMNI and site.university_zh:
            data["alumniOf"] = _compact(
                {
                    "@type": "CollegeOrUniversity",
                    "name": site.university_zh,
                    "alternateName": site.university_en,
                }
            )

        if person.email_public:
            data["email"] = person.email_public

        # knowsAbout：研究技能。只有在頁面顯示 tag 時才輸出。
        if person.skills:
            data["knowsAbout"] = person.skills

        # sameAs：頁面上實際可點擊的外部識別連結。
        sameas = [
            url
            for url in (
                person.orcid_url,
                person.scholar_url,
                person.github_url,
                person.linkedin_url,
                person.external_url,
            )
            if url
        ]
        if sameas:
            data["sameAs"] = sameas

        # 就業資訊：只有明確同意公開時（SAI §5.4 隱私規則）。
        destination = person.public_destination
        if destination and person.current_affiliation:
            data["worksFor"] = {"@type": "Organization", "name": person.current_affiliation}

        return _compact(data)

    # ------------------------------------------------------------------
    # ResearchOutput
    # ------------------------------------------------------------------
    @staticmethod
    def research_output(output: ResearchOutput) -> dict:
        """成果 JSON-LD。

        型別選擇（SAI §12.2）：
          journal / conference -> ScholarlyArticle [S14]
          其餘                 -> CreativeWork [S15]
          dataset              -> Dataset（只有真的公開 dataset 才用）

        絕不輸出的欄位：
          citation count、impact factor、任何未填寫的 DOI。
        """
        from app.models.mixins import OutputType

        url = SEOService.absolute_url(url_for("public.research_detail", slug=output.slug))

        if output.output_type == OutputType.DATASET:
            schema_type = "Dataset"
        elif output.is_scholarly:
            schema_type = "ScholarlyArticle"
        else:
            schema_type = "CreativeWork"

        data = {
            "@type": schema_type,
            "@id": url + "#output",
            "name": output.display_title,
            "url": url,
            "inLanguage": "zh-Hant-TW",
        }

        # headline 僅對 Article 家族有意義。
        if schema_type == "ScholarlyArticle":
            data["headline"] = output.display_title

        if output.title_en and output.title_en != output.display_title:
            data["alternateName"] = output.title_en

        description = output.summary_zh or output.summary_en
        if description:
            data["description"] = SEOService.truncate_description(description, max_length=300)

        # 日期：有正式出版日期用它，否則只給年份。
        if output.publication_date:
            data["datePublished"] = output.publication_date.isoformat()
        elif output.year:
            # schema.org 接受 ISO 8601 的年份精度。
            data["datePublished"] = str(output.year)

        # 作者：Lab 內部人物用 @id 連結；外部作者用純名稱。
        authors: list[dict] = []
        for person in output.public_lab_people:
            authors.append(
                {
                    "@type": "Person",
                    "@id": SEOService.absolute_url(
                        url_for("public.person_detail", slug=person.slug)
                    )
                    + "#person",
                    "name": person.name_zh,
                }
            )
        if authors:
            data["author"] = authors
        elif output.authors_display_text:
            # 沒有 Lab 人物關聯但有作者列時，輸出純文字作者。
            # 這仍是頁面可見內容，符合 [S7]。
            data["author"] = {"@type": "Person", "name": output.authors_display_text}

        if output.keywords:
            data["keywords"] = output.keywords

        if output.venue:
            # 保守處理：不宣告是 Periodical 還是 Event，
            # 因為 venue 欄位是自由文字，無法可靠判斷。
            data["publisher"] = {"@type": "Organization", "name": output.venue}

        if output.doi:
            data["identifier"] = f"https://doi.org/{output.doi}"

        external_links = [
            link
            for link in (output.doi_url, output.external_url, output.github_url, output.dataset_url)
            if link
        ]
        if external_links:
            data["sameAs"] = external_links

        image = _image_url(output.hero_image_path)
        if image:
            data["image"] = image

        data["isPartOf"] = {"@id": SchemaService.organization_id()}

        return _compact(data)

    # ------------------------------------------------------------------
    # 導覽類
    # ------------------------------------------------------------------
    @staticmethod
    def breadcrumb(items, current_name: str, current_url: str) -> dict | None:
        """BreadcrumbList JSON-LD（SAI §12.2）。

        Args:
            items: [(名稱, 絕對URL), ...] 祖先節點。
            current_name / current_url: 當前頁。

        Returns:
            BreadcrumbList dict；只有一層時回 None
            （單層麵包屑沒有資訊價值，輸出反而是雜訊）。

        重要：麵包屑必須同時出現在頁面可見導覽中 [S7]。
        本專案的 base.html 會 render 相同的 breadcrumbs 資料。
        """
        nodes = list(items or [])
        if not nodes:
            return None

        elements = []
        position = 1
        for name, url in nodes:
            elements.append(
                {
                    "@type": "ListItem",
                    "position": position,
                    "name": name,
                    "item": url,
                }
            )
            position += 1

        elements.append(
            {
                "@type": "ListItem",
                "position": position,
                "name": current_name,
                "item": current_url,
            }
        )

        return {"@type": "BreadcrumbList", "itemListElement": elements}

    @staticmethod
    def collection_page(name: str, url: str, description: str, item_urls: list[str]) -> dict:
        """列表頁的 CollectionPage。

        只列出 URL 而非完整實體，避免同一實體在多處重複宣告。
        """
        data = {
            "@type": "CollectionPage",
            "@id": url + "#collection",
            "name": name,
            "url": url,
            "description": description,
            "isPartOf": {"@id": f"{SEOService.base_url()}/#website"},
        }
        if item_urls:
            data["mainEntity"] = {
                "@type": "ItemList",
                "numberOfItems": len(item_urls),
                "itemListElement": [
                    {"@type": "ListItem", "position": i + 1, "url": u}
                    for i, u in enumerate(item_urls)
                ],
            }
        return _compact(data)

    # ------------------------------------------------------------------
    # 組合輸出
    # ------------------------------------------------------------------
    @staticmethod
    def graph(*blocks) -> dict:
        """把多個實體合併為單一 @graph（見檔頭「特殊機制」）。

        None 會被略過，讓呼叫端可以寫
        `SchemaService.graph(org, website, breadcrumb_or_none)`
        而不必先過濾。
        """
        nodes = [b for b in blocks if b]
        return {"@context": _CONTEXT, "@graph": nodes}
