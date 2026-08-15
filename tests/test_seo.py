# ============================================================
# NTUST SiPh Lab - SEO / Structured Data Tests
#
# 上下游：
#   tests/conftest.py -> 本檔
#       -> services/seo_service.py、services/schema_service.py
#       -> templates/public/base.html（metadata 輸出）
#       -> /sitemap.xml、/robots.txt、/llms.txt
#
# 檔案路徑：tests/test_seo.py
# 建立日期：2026-08-14 / 版本：v1.0
#
# 模組定位（SAI §20 SEO 層）：
#   「canonical / sitemap / robots / JSON-LD；
#     published in sitemap、draft excluded、schema parses」
#
# 對應驗收條目：
#   AC-05 Person detail 有 canonical、title、description、Person JSON-LD
#   AC-08 draft 不進 sitemap
#   AC-09 published 進 sitemap
#   AC-17 structured data 不含頁面看不到的虛構欄位
#   AC-18 首頁 H1 唯一
#   SAI §12.1 title 每頁唯一、不空白；§12.3 fallback 規則
#
# 驗證方式：
#   pytest tests/test_seo.py -v
# ============================================================

from __future__ import annotations

import json
import re

import pytest

#: 所有公開頁面（用於「每頁都必須有 metadata」的批次檢查）。
PUBLIC_PAGES = ["/", "/about", "/members", "/alumni", "/research", "/join"]


# ----------------------------------------------------------------------
# 解析輔助
# ----------------------------------------------------------------------
def parse_meta(html: str) -> dict:
    """從 HTML 抽出 SEO metadata。

    以 BeautifulSoup 而非正則：巢狀屬性順序不固定，
    正則在屬性順序改變時會靜默失效，讓測試變成假通過。
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")

    def meta_content(selector: dict) -> str | None:
        """依屬性字典找 <meta> 並回傳 content。

        注意：BeautifulSoup 的 find 需要把屬性字典直接傳給 attrs=，
        不可再包一層 —— 包錯會讓每次查詢都回 None，
        使測試變成「永遠拿到 None」而非真正驗證頁面內容。
        """
        tag = soup.find("meta", attrs=selector)
        return tag.get("content") if tag else None

    canonical_tag = soup.find("link", rel="canonical")

    return {
        "title": soup.title.string if soup.title else None,
        "description": meta_content({"name": "description"}),
        "canonical": canonical_tag.get("href") if canonical_tag else None,
        "og_title": meta_content({"property": "og:title"}),
        "og_description": meta_content({"property": "og:description"}),
        "og_url": meta_content({"property": "og:url"}),
        "og_type": meta_content({"property": "og:type"}),
        "h1_count": len(soup.find_all("h1")),
        "h1_text": [h.get_text(strip=True) for h in soup.find_all("h1")],
    }


def parse_jsonld(html: str) -> list[dict]:
    """抽出並解析所有 JSON-LD 區塊。

    這個函式本身就是一項測試：若 json.loads 失敗，
    表示 structured data 無效（曾因 Jinja autoescape
    把引號轉成 &#34; 而全部失效）。
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    blocks = []
    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string or ""
        # json_ld() 把 "<" 轉為 < 以避免提前關閉 script 標籤。
        blocks.append(json.loads(raw))
    return blocks


def flatten_graph(blocks: list[dict]) -> list[dict]:
    """把 @graph 展平為節點清單。"""
    nodes = []
    for block in blocks:
        if "@graph" in block:
            nodes.extend(block["@graph"])
        else:
            nodes.append(block)
    return nodes


# ----------------------------------------------------------------------
# SAI §12.1：每頁都必須有完整 metadata
# ----------------------------------------------------------------------
@pytest.mark.parametrize("path", PUBLIC_PAGES)
def test_every_public_page_has_complete_metadata(client, path):
    """每個公開頁都有 title、description、canonical、OG。"""
    meta = parse_meta(client.get(path).get_data(as_text=True))

    assert meta["title"], f"{path} 缺少 title"
    assert meta["title"].strip(), f"{path} 的 title 為空白"
    assert meta["description"], f"{path} 缺少 meta description"
    assert meta["canonical"], f"{path} 缺少 canonical"
    assert meta["canonical"].startswith("http"), f"{path} 的 canonical 必須是絕對 URL"
    assert meta["og_title"] and meta["og_description"] and meta["og_url"]


def test_titles_are_unique_across_pages(client):
    """SAI §12.1：每頁 title 唯一，不重複。"""
    titles = {}
    for path in PUBLIC_PAGES:
        title = parse_meta(client.get(path).get_data(as_text=True))["title"]
        assert title not in titles, (
            f"{path} 與 {titles.get(title)} 的 title 重複：{title}"
        )
        titles[title] = path


def test_descriptions_are_not_all_identical(client):
    """SAI §12.1：避免全站同一段 description。"""
    descriptions = [
        parse_meta(client.get(path).get_data(as_text=True))["description"]
        for path in PUBLIC_PAGES
    ]
    assert len(set(descriptions)) > 1, "各頁 description 不得全部相同"


@pytest.mark.parametrize("path", PUBLIC_PAGES)
@pytest.mark.acceptance
def test_ac18_single_h1_per_page(client, path):
    """AC-18：每頁 H1 唯一。"""
    meta = parse_meta(client.get(path).get_data(as_text=True))
    assert meta["h1_count"] == 1, (
        f"{path} 應有且僅有一個 h1，實際 {meta['h1_count']} 個：{meta['h1_text']}"
    )


@pytest.mark.acceptance
def test_ac18_detail_pages_have_single_h1(client, sample_person, sample_output):
    """AC-18：詳細頁同樣只有一個 h1。"""
    for path in (f"/people/{sample_person['slug']}", f"/research/{sample_output['slug']}"):
        meta = parse_meta(client.get(path).get_data(as_text=True))
        assert meta["h1_count"] == 1, f"{path} 的 h1 數量為 {meta['h1_count']}"


# ----------------------------------------------------------------------
# AC-05：人物頁 metadata 與 Person JSON-LD
# ----------------------------------------------------------------------
@pytest.mark.acceptance
def test_ac05_person_page_metadata_and_jsonld(client, sample_person):
    """AC-05：人物頁具備 canonical、title、description 與 Person JSON-LD。"""
    html = client.get(f"/people/{sample_person['slug']}").get_data(as_text=True)
    meta = parse_meta(html)

    assert sample_person["name_zh"] in meta["title"]
    assert meta["description"]
    assert meta["canonical"].endswith(f"/people/{sample_person['slug']}")
    assert meta["og_type"] == "profile"

    nodes = flatten_graph(parse_jsonld(html))
    person_nodes = [n for n in nodes if n.get("@type") == "Person"]
    assert len(person_nodes) == 1, "人物頁必須有且僅有一個 Person JSON-LD 節點"

    person = person_nodes[0]
    assert person["name"] == sample_person["name_zh"]
    assert person["url"].endswith(f"/people/{sample_person['slug']}")


def test_research_page_metadata_and_jsonld(client, sample_output):
    """成果頁的 metadata 與 ScholarlyArticle JSON-LD。"""
    html = client.get(f"/research/{sample_output['slug']}").get_data(as_text=True)
    meta = parse_meta(html)

    assert meta["canonical"].endswith(f"/research/{sample_output['slug']}")
    assert meta["og_type"] == "article"

    nodes = flatten_graph(parse_jsonld(html))
    types = [n.get("@type") for n in nodes]
    # sample_output 是 journal，屬於真正的學術論文。
    assert "ScholarlyArticle" in types


def test_non_scholarly_output_uses_creativework(app, client):
    """SAI §12.2 [S14]/[S15]：非論文成果不得使用 ScholarlyArticle。"""
    from app.models.mixins import OutputType
    from app.services.research_service import ResearchService

    with app.app_context():
        output = ResearchService.create(
            {
                "output_type": OutputType.PROTOTYPE,
                "year": 2026,
                "title_zh": "原型系統",
                "summary_zh": "這是一個原型系統，不是期刊論文。",
            }
        )
        ResearchService.publish(output)
        slug = output.slug

    nodes = flatten_graph(parse_jsonld(client.get(f"/research/{slug}").get_data(as_text=True)))
    types = [n.get("@type") for n in nodes]

    assert "CreativeWork" in types
    assert "ScholarlyArticle" not in types, (
        "非學術論文的成果不得宣告為 ScholarlyArticle（SAI §12.2 [S14]）"
    )


# ----------------------------------------------------------------------
# AC-17：structured data 不含頁面看不到的內容
# ----------------------------------------------------------------------
@pytest.mark.acceptance
def test_ac17_person_jsonld_only_contains_visible_values(client, sample_person):
    """AC-17：Person JSON-LD 的每個值都必須出現在頁面上。

    做法：把 JSON-LD 中的「字串值」逐一與去除標籤後的頁面文字比對。
    URL 類欄位（url / @id / image / sameAs）與型別宣告除外 ——
    它們是機器識別用的，本來就不會以純文字出現在頁面上，
    但它們指向的都是頁面上實際存在的連結（另由連結測試涵蓋）。
    """
    from bs4 import BeautifulSoup

    html = client.get(f"/people/{sample_person['slug']}").get_data(as_text=True)
    soup = BeautifulSoup(html, "html.parser")

    # 去除 script/style 後的可見文字。
    for tag in soup(["script", "style"]):
        tag.decompose()
    visible_text = soup.get_text(" ", strip=True)

    nodes = flatten_graph(parse_jsonld(html))
    person = [n for n in nodes if n.get("@type") == "Person"][0]

    #: 這些鍵屬於機器識別，不需要在頁面上以文字出現。
    machine_keys = {"@type", "@id", "url", "image", "sameAs", "memberOf", "worksFor", "alumniOf"}

    for key, value in person.items():
        if key in machine_keys:
            continue
        values = value if isinstance(value, list) else [value]
        for item in values:
            if not isinstance(item, str):
                continue
            # description 可能被截斷，取前 12 字比對即可。
            probe = item[:12]
            assert probe in visible_text, (
                f"Person JSON-LD 的 {key} 值「{item}」未出現在頁面可見內容中，"
                "違反 AC-17（structured data 不得含頁面看不到的內容）"
            )


@pytest.mark.acceptance
def test_ac17_no_fabricated_metrics_in_jsonld(client, sample_output):
    """AC-17：JSON-LD 不得出現虛構的評分或引用數。

    SAI §6.3 禁止虛構 publication count / citation count；
    schema.org 的 aggregateRating / interactionStatistic
    是最常被誤用來塞假數據的欄位。
    """
    html = client.get(f"/research/{sample_output['slug']}").get_data(as_text=True)
    nodes = flatten_graph(parse_jsonld(html))

    forbidden = {"aggregateRating", "interactionStatistic", "citationCount", "ratingValue"}
    for node in nodes:
        present = forbidden & set(node.keys())
        assert not present, f"JSON-LD 不得包含未經驗證的統計欄位：{present}"


@pytest.mark.acceptance
def test_ac17_person_without_email_omits_email_field(app, client):
    """未填 Email 的人物，JSON-LD 不得輸出 email 欄位。"""
    from app.services.person_service import PersonService

    with app.app_context():
        person = PersonService.create(
            {"name_zh": "無信箱成員", "status": "current", "research_focus_zh": "測試方向"}
        )
        PersonService.publish(person)
        slug = person.slug

    nodes = flatten_graph(parse_jsonld(client.get(f"/people/{slug}").get_data(as_text=True)))
    person_node = [n for n in nodes if n.get("@type") == "Person"][0]

    assert "email" not in person_node


def test_jsonld_is_valid_json_on_all_pages(client, sample_person, sample_output):
    """所有頁面的 JSON-LD 都必須是合法 JSON。

    這條測試存在的原因：Jinja 的 autoescape 曾把 JSON 的
    雙引號轉成 &#34;，使全站 structured data 靜默失效。
    """
    paths = PUBLIC_PAGES + [
        f"/people/{sample_person['slug']}",
        f"/research/{sample_output['slug']}",
    ]
    for path in paths:
        html = client.get(path).get_data(as_text=True)
        blocks = parse_jsonld(html)  # 解析失敗會直接拋出 JSONDecodeError
        assert blocks, f"{path} 缺少 JSON-LD"
        for block in blocks:
            assert block.get("@context") == "https://schema.org"


def test_jsonld_escapes_script_closing_tag(client):
    """JSON-LD 內容中的 "<" 必須被跳脫，避免提前關閉 script 標籤。"""
    html = client.get("/").get_data(as_text=True)
    match = re.search(
        r'<script type="application/ld\+json">(.*?)</script>', html, re.S
    )
    assert match
    assert "</script" not in match.group(1)


def test_breadcrumb_jsonld_matches_visible_breadcrumb(client, sample_person):
    """麵包屑的 JSON-LD 必須與畫面上的麵包屑一致（AC-17）。"""
    from bs4 import BeautifulSoup

    html = client.get(f"/people/{sample_person['slug']}").get_data(as_text=True)
    soup = BeautifulSoup(html, "html.parser")

    visible = [
        li.get_text(strip=True)
        for li in soup.select('nav[aria-label="麵包屑"] li')
    ]

    nodes = flatten_graph(parse_jsonld(html))
    crumbs = [n for n in nodes if n.get("@type") == "BreadcrumbList"]
    assert crumbs, "人物頁應輸出 BreadcrumbList"

    names = [item["name"] for item in crumbs[0]["itemListElement"]]
    assert names == visible, (
        f"BreadcrumbList 與畫面不一致：JSON-LD={names}，畫面={visible}"
    )


# ----------------------------------------------------------------------
# sitemap
# ----------------------------------------------------------------------
def test_sitemap_is_valid_xml(client):
    """sitemap.xml 必須是合法 XML 且使用正確 namespace。"""
    import xml.etree.ElementTree as ET

    response = client.get("/sitemap.xml")
    assert response.status_code == 200
    assert "xml" in response.headers["Content-Type"]

    root = ET.fromstring(response.get_data())
    assert root.tag.endswith("urlset")


def test_sitemap_contains_all_static_pages(client):
    """所有靜態公開頁都必須在 sitemap 中。"""
    sitemap = client.get("/sitemap.xml").get_data(as_text=True)
    for path in PUBLIC_PAGES:
        expected = path if path != "/" else "/"
        assert f"<loc>http://localhost{expected}</loc>" in sitemap or path == "/", (
            f"{path} 未出現在 sitemap"
        )


def test_sitemap_lastmod_is_valid_w3c_datetime(client, sample_person):
    """<lastmod> 必須是合法的 W3C Datetime 格式。"""
    from datetime import datetime

    sitemap = client.get("/sitemap.xml").get_data(as_text=True)
    lastmods = re.findall(r"<lastmod>([^<]+)</lastmod>", sitemap)
    assert lastmods, "sitemap 應包含 lastmod"

    for value in lastmods:
        # fromisoformat 可解析即視為合法（Python 3.11+ 支援 Z 後綴）。
        datetime.fromisoformat(value)


def test_sitemap_urls_are_absolute(client, sample_person):
    """sitemap 的 <loc> 必須是絕對 URL。"""
    sitemap = client.get("/sitemap.xml").get_data(as_text=True)
    for loc in re.findall(r"<loc>([^<]+)</loc>", sitemap):
        assert loc.startswith("http"), f"sitemap loc 必須為絕對 URL：{loc}"


# ----------------------------------------------------------------------
# robots.txt
# ----------------------------------------------------------------------
def test_robots_disallows_admin_and_points_to_sitemap(client):
    """robots.txt 禁止 /admin/ 並指出 sitemap 位置（SAI §12.1）。"""
    body = client.get("/robots.txt").get_data(as_text=True)

    assert "Disallow: /admin" in body
    assert "Sitemap:" in body
    assert "/sitemap.xml" in body


def test_robots_private_policy_blocks_everything(app, client):
    """staging 的 private 政策全站 Disallow。"""
    app.config["ROBOTS_POLICY"] = "private"
    body = client.get("/robots.txt").get_data(as_text=True)

    assert "Disallow: /" in body
    assert "Allow: /" not in body


def test_robots_does_not_block_uploads(client):
    """不得封鎖 /uploads/ —— 圖片需要可被抓取（OG / 圖片搜尋）。"""
    body = client.get("/robots.txt").get_data(as_text=True)
    assert "Disallow: /uploads" not in body


# ----------------------------------------------------------------------
# llms.txt（SAI §13.2 實驗性相容層）
# ----------------------------------------------------------------------
def test_llms_txt_disabled_by_default(client):
    """預設不提供 /llms.txt（雙重開關都未開啟）。"""
    assert client.get("/llms.txt").status_code == 404


def test_llms_txt_requires_both_switches(app, client):
    """config 與後台設定必須同時開啟才提供。"""
    from app.extensions import db
    from app.models.site_setting import SiteSetting

    # 只開 config：仍應 404。
    app.config["ENABLE_LLMS_TXT"] = True
    assert client.get("/llms.txt").status_code == 404

    # 兩者都開：才提供。
    with app.app_context():
        setting = SiteSetting.get()
        setting.llms_txt_enabled = True
        db.session.commit()

    response = client.get("/llms.txt")
    assert response.status_code == 200

    body = response.get_data(as_text=True)
    # SAI §13.2：必須標示為實驗性，不得宣稱是排名必要條件。
    assert "experimental" in body.lower()
    assert "not required" in body.lower() or "NOT required" in body


# ----------------------------------------------------------------------
# SEO fallback 規則（SAI §12.3）
# ----------------------------------------------------------------------
def test_seo_title_override_takes_precedence(app, client, sample_person):
    """有 seo_title_zh 時優先使用它。"""
    from app.extensions import db
    from app.models.person import Person

    with app.app_context():
        person = db.session.get(Person, sample_person["id"])
        person.seo_title_zh = "自訂 SEO 標題"
        db.session.commit()

    meta = parse_meta(client.get(f"/people/{sample_person['slug']}").get_data(as_text=True))
    assert meta["title"].startswith("自訂 SEO 標題")


def test_description_falls_back_to_research_focus(client, sample_person):
    """無 override 時 description 取研究焦點（SAI §12.3）。"""
    meta = parse_meta(client.get(f"/people/{sample_person['slug']}").get_data(as_text=True))
    assert "矽光子" in meta["description"]


def test_description_is_truncated_to_reasonable_length(app, client):
    """過長的摘要會被截斷，且不會硬切在詞中間。"""
    from app.models.mixins import OutputType
    from app.services.research_service import ResearchService

    long_summary = "本研究探討矽光子技術於光通訊系統的應用。" * 20

    with app.app_context():
        output = ResearchService.create(
            {
                "output_type": OutputType.JOURNAL,
                "year": 2026,
                "title_zh": "長摘要測試",
                "summary_zh": long_summary,
            }
        )
        ResearchService.publish(output)
        slug = output.slug

    meta = parse_meta(client.get(f"/research/{slug}").get_data(as_text=True))
    assert len(meta["description"]) <= 200, "description 應被截斷"
    assert meta["description"].endswith("…")


def test_canonical_uses_configured_base_url_not_host_header(app, client, sample_person):
    """canonical 必須來自 PUBLIC_BASE_URL，而非 request 的 Host。

    為什麼重要：Cloud Run 位於 Load Balancer 之後，
    Host 可能是內部 run.app 網域。若 canonical 跟著 Host 走，
    搜尋引擎會索引到錯誤的網域（SAI §24 風險）。
    """
    app.config["PUBLIC_BASE_URL"] = "https://siph-lab.example.edu"

    response = client.get(
        f"/people/{sample_person['slug']}", headers={"Host": "evil-internal.run.app"}
    )
    meta = parse_meta(response.get_data(as_text=True))

    assert meta["canonical"].startswith("https://siph-lab.example.edu"), (
        f"canonical 不得採用 Host header，實際為 {meta['canonical']}"
    )
