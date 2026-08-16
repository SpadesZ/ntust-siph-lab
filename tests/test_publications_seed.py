# ============================================================
# NTUST SiPh Lab - Publication Seed Tests
#
# 檔案路徑：tests/test_publications_seed.py
# 建立日期：2026-08-15 / 版本：v1.0
#
# 模組定位（SAI §2.3、§5.3、§12.2、§13.1）：
#   scripts/seed_publications.py 是「另有 Lab 確認來源」的內容
#   匯入路徑。它與 seed_from_google_sites 的分野必須清楚：
#   母站沒有研究成果，那支腳本正確地拒絕產生；這九篇論文
#   由研究室提供並經 Crossref 查證，屬於不同來源。
#
# 重點：
#   1. 九篇論文的書目欄位完整（DOI / 年份 / venue / 作者列）
#   2. 全部關聯到教授，形成雙向導航（§13.1）
#   3. journal/conference 正確輸出 ScholarlyArticle（§12.2 [S14]）
#   4. 冪等：重複執行不產生重複資料
#   5. 摘要不得出現未經查證的數值或結論（§2.3）
#
# 驗證方式：
#   pytest tests/test_publications_seed.py -v
# ============================================================

from __future__ import annotations

import re

import pytest

from scripts.seed_publications import PUBLICATIONS, run_seed


@pytest.fixture()
def seeded_lab(app):
    """先匯入母站內容（建立教授），再匯入論文。"""
    from app.extensions import db
    from scripts.seed_from_google_sites import run_seed as seed_legacy

    with app.app_context():
        seed_legacy()
        db.session.commit()
        run_seed()
        db.session.commit()
    return app


# ----------------------------------------------------------------------
# 資料完整性
# ----------------------------------------------------------------------
def test_all_publications_have_required_bibliography():
    """每篇都必須有 DOI、年份、venue、作者列 —— 這是可查證性的基礎。"""
    for entry in PUBLICATIONS:
        assert entry["doi"], entry["slug"]
        assert entry["year"], entry["slug"]
        assert entry["venue"], entry["slug"]
        assert entry["authors"], entry["slug"]
        assert entry["title_en"], entry["slug"]


def test_dois_are_normalised_and_unique():
    """DOI 必須是正規化裸值且不重複。"""
    from app.models.research_output import ResearchOutput

    dois = [e["doi"] for e in PUBLICATIONS]
    assert len(dois) == len(set(dois)), "DOI 不得重複"
    for doi in dois:
        assert ResearchOutput.normalize_doi(doi) == doi, f"DOI 未正規化：{doi}"


def test_every_publication_lists_the_professor_as_author():
    """作者列必須包含教授本人 —— 否則不該出現在他的著作清單。"""
    for entry in PUBLICATIONS:
        assert "Yang" in entry["authors"], entry["slug"]


#: 中文欄位裡出現的效能數值都必須能在 abstract_en 找到。
_METRIC = re.compile(
    r"\d+(?:\.\d+)?\s*(?:dB|dBm|%|GHz|MHz|nm|μm|um|Gb/s|Gbit/s|Gbps|mW|ms|km|m\b)",
    re.IGNORECASE,
)
#: 中文欄位裡需要比對的欄位。
_ZH_FIELDS = ("summary_zh", "problem_zh", "method_zh", "results_zh", "significance_zh")


def _metric_numbers(text: str) -> set[str]:
    """抽出「帶單位」的數值本體，用於檢查中文欄位寫了哪些效能數字。"""
    return {m.rstrip(".") for m in
            (re.sub(r"[^\d.]", "", x) for x in _METRIC.findall(text))} - {""}


def _all_numbers(text: str) -> set[str]:
    """抽出來源文字中的「所有」數值，作為可接受的依據集合。

    刻意不要求來源端的數值緊鄰單位。原文的寫法變化很多：
      "0.5-dB"（連字號）、"<10 to >50 km"（數值與單位被文字隔開）、
      "10-9"（指數）。
    若來源端也用帶單位的樣式去抓，這些合法數值會被誤判成「查無依據」。
    來源端放寬、中文端收緊，才是正確的方向 ——
    我們要擋的是「憑空出現的數字」，不是「單位寫法不同」。
    """
    return {n.rstrip(".") for n in re.findall(r"\d+(?:\.\d+)?", text)} - {""}


def test_every_publication_records_its_abstract_source():
    """每篇都必須留下摘要來源，否則無法查核中文內容的依據。"""
    for entry in PUBLICATIONS:
        assert entry.get("abstract_en"), f"{entry['slug']} 缺少 abstract_en"
        assert entry.get("abstract_source"), f"{entry['slug']} 缺少 abstract_source"
        assert re.search(r"\d{4}-\d{2}-\d{2}", entry["abstract_source"]), (
            f"{entry['slug']} 的 abstract_source 必須含取得日期"
        )


def test_metrics_are_traceable_to_abstract():
    """中文欄位中的每一個效能數值，都必須在該篇 abstract_en 中找得到。

    這條取代了原本「摘要一律不得出現數值」的規則。

    原規則的用意是防止捏造 —— 當時九篇只有標題可依據，沒有摘要，
    任何數字都必然是編的。現在每篇都有出版方登錄的英文摘要原文
    （abstract_en，來源與日期記錄於 abstract_source），
    數值不再是「無中生有」而是「可查證」。

    但「可以有數值」不等於「可以隨便寫數值」，所以規則改成更強的版本：
    數值必須真的出現在該篇摘要原文裡。這樣既允許忠實轉述，
    又能擋下打錯字、記錯單位、或把別篇的數字搬過來。

    注意這裡比對的是數值本體而非單位字串，因為中文會把
    "10 Gb/s" 寫成「10-Gb/s」、"1.55 um" 寫成「1.55 μm」，
    單位寫法不同但數值必須一致。
    """
    problems = []
    for entry in PUBLICATIONS:
        # 來源：摘要原文 + 標題（標題載明的數值同樣算有依據）。
        source_numbers = (
            _all_numbers(entry["abstract_en"])
            | _all_numbers(entry["title_en"])
            | _all_numbers(entry["title_zh"])
        )
        for field in _ZH_FIELDS:
            for value in _metric_numbers(entry.get(field) or ""):
                if value not in source_numbers:
                    problems.append(
                        f"{entry['slug']}.{field} 的數值 {value!r} "
                        f"在 abstract_en 中找不到依據"
                    )

    assert not problems, "以下數值無法回溯到摘要原文：\n  " + "\n  ".join(problems)


def test_four_part_research_body_present():
    """SAI §5.3：成果頁必須能回答 Problem / Method / Results / Significance。"""
    for entry in PUBLICATIONS:
        for field in ("problem_zh", "method_zh", "results_zh", "significance_zh"):
            assert (entry.get(field) or "").strip(), f"{entry['slug']} 缺少 {field}"


def test_summary_is_not_merely_the_title_restated():
    """摘要不得只是標題的改寫。

    這是設計審查指出的「全站最大的一處同義重複」。
    以長度與資訊量作最低門檻：中文摘要必須明顯長於中文標題，
    否則代表它沒有講出標題以外的東西。
    """
    for entry in PUBLICATIONS:
        summary = entry["summary_zh"]
        title = entry["title_zh"]
        assert len(summary) >= len(title) * 2, (
            f"{entry['slug']} 的摘要（{len(summary)} 字）相對標題"
            f"（{len(title)} 字）過短，可能只是標題的改寫"
        )


def test_slugs_are_url_safe_and_unique():
    slugs = [e["slug"] for e in PUBLICATIONS]
    assert len(slugs) == len(set(slugs))
    for slug in slugs:
        assert re.fullmatch(r"[a-z0-9-]+", slug), slug


# ----------------------------------------------------------------------
# 匯入結果
# ----------------------------------------------------------------------
def test_seed_publishes_all_entries(seeded_lab):
    from app.extensions import db
    from app.models.mixins import PublishStatus
    from app.models.research_output import ResearchOutput

    with seeded_lab.app_context():
        outputs = db.session.query(ResearchOutput).all()
        assert len(outputs) == len(PUBLICATIONS)
        for o in outputs:
            assert o.publish_status == PublishStatus.PUBLISHED, o.slug


def test_publications_appear_on_research_index(seeded_lab, client):
    html = client.get("/research").get_data(as_text=True)
    for entry in PUBLICATIONS:
        assert entry["slug"] in html, f"{entry['slug']} 未出現於 /research"


def test_publications_are_in_sitemap(seeded_lab, client):
    sitemap = client.get("/sitemap.xml").get_data(as_text=True)
    for entry in PUBLICATIONS:
        assert entry["slug"] in sitemap, f"{entry['slug']} 未收錄於 sitemap"


#: 個人頁「相關研究成果」最多列出的筆數，與 person_detail.html 一致。
#: 超過時改列最新幾筆並提供「查看全部 N 筆成果 →」。
RELATED_OUTPUTS_LIMIT = 5


def test_bidirectional_link_with_professor(seeded_lab, client):
    """SAI §13.1 Cross-entity linking：Person <-> ResearchOutput 雙向可導航。

    這個測試原本斷言「教授頁必須列出全部 9 筆成果」。
    設計審查後改為現在的形式，理由：

      本研究室所有成果的作者都是同一位教授，因此「列出全部」
      等於把 /research 整頁在個人頁再抄一次 —— 那正是本專案
      首要原則「資訊不要重複出現」要避免的。

      AC-07 與 §13.1 要求的是「雙向可導航」，不是「完整複製」。
      因此改為驗證真正的契約：
        1. 個人頁列出最新的幾筆（可直接點擊）；
        2. 未列出的部分有明確且可點擊的完整清單入口；
        3. 成果頁反向連回人物頁。
      三者成立即滿足雙向導航，且不重複整份清單。

    若日後把上限改掉，請同步更新 person_detail.html 與此常數。
    """
    person = client.get("/people/chun-liang-yang").get_data(as_text=True)

    listed = [e["slug"] for e in PUBLICATIONS if e["slug"] in person]
    assert listed, "教授頁必須至少列出一筆相關成果"
    assert len(listed) <= RELATED_OUTPUTS_LIMIT, (
        f"個人頁最多列 {RELATED_OUTPUTS_LIMIT} 筆，實際 {len(listed)} 筆"
    )

    # 未完整列出時，必須提供通往完整清單的入口，否則就是死路。
    if len(listed) < len(PUBLICATIONS):
        assert url_for_research_index() in person, (
            "成果未完整列出時，個人頁必須提供前往 /research 的完整清單連結"
        )
        assert f"{len(PUBLICATIONS)}" in person, "應標示完整筆數，讓使用者知道還有多少"

    # 反向：成果頁連回人物頁。
    detail = client.get(f"/research/{PUBLICATIONS[0]['slug']}").get_data(as_text=True)
    assert "/people/chun-liang-yang" in detail


def url_for_research_index() -> str:
    """/research 的路徑。獨立成函式以免測試散落硬編碼字串。"""
    return "/research"


# ----------------------------------------------------------------------
# 論文標題以原文為主、中英並列
# ----------------------------------------------------------------------
# 這九篇都發表於英文期刊/國際會議並具 DOI。英文標題是它們唯一可被
# 引用與檢索的正式名稱；中文標題是閱讀輔助的翻譯。
# 若把翻譯當主標題，訪客用論文原名搜尋不會命中本站，
# 引用本站的人也會抄到一個不存在的標題。


def test_scholarly_titles_use_original_english_as_primary(seeded_lab):
    """期刊/會議論文的主標題必須是英文原文，不是中文翻譯。"""
    from app.extensions import db
    from app.models.research_output import ResearchOutput

    with seeded_lab.app_context():
        outputs = db.session.query(ResearchOutput).all()
        assert outputs, "seed 應建立成果"
        for o in outputs:
            if not o.is_scholarly or not o.title_en:
                continue
            assert o.display_title == o.title_en, (
                f"{o.slug} 的主標題應為英文原文，實際為 {o.display_title!r}"
            )
            # 中文譯名不得因此消失 —— 必須仍以並列標題呈現。
            assert o.secondary_title == o.title_zh, (
                f"{o.slug} 的中文標題必須仍以並列標題保留"
            )
            assert o.display_title_lang == "en"
            assert o.secondary_title_lang == "zh-Hant-TW"


def test_both_titles_rendered_on_public_pages(seeded_lab, client):
    """中英標題必須同時出現在成果列表與詳細頁（不得只剩一種）。"""
    from app.extensions import db
    from app.models.research_output import ResearchOutput

    with seeded_lab.app_context():
        sample = db.session.query(ResearchOutput).filter_by(
            slug=PUBLICATIONS[0]["slug"]
        ).one()
        title_en, title_zh = sample.title_en, sample.title_zh

    for path in ("/research", f"/research/{PUBLICATIONS[0]['slug']}"):
        html = client.get(path).get_data(as_text=True)
        assert title_en in html, f"{path} 缺少英文原文標題"
        assert title_zh in html, f"{path} 缺少中文標題"


def test_scholarly_jsonld_keeps_chinese_title_and_english_language(seeded_lab, client):
    """結構化資料：name 為原文、alternateName 為中譯、inLanguage 為 en。"""
    import json
    import re

    html = client.get(f"/research/{PUBLICATIONS[0]['slug']}").get_data(as_text=True)
    blocks = re.findall(
        r'<script type="application/ld\+json">(.*?)</script>', html, re.S
    )
    article = None
    for block in blocks:
        for node in json.loads(block).get("@graph", []):
            if node.get("@type") == "ScholarlyArticle":
                article = node
    assert article, "找不到 ScholarlyArticle 節點"
    assert article["name"] == PUBLICATIONS[0]["title_en"]
    # 中文標題不得只存在於頁面而從結構化資料消失。
    assert article.get("alternateName"), "中譯標題必須以 alternateName 保留"
    assert article["inLanguage"] == "en", "英文論文的 inLanguage 不應標為中文"


def test_journal_outputs_emit_scholarly_article(seeded_lab, client):
    """SAI §12.2 [S14]：只有真正的學術論文才用 ScholarlyArticle。"""
    journal = next(e for e in PUBLICATIONS if e["output_type"] == "journal")
    html = client.get(f"/research/{journal['slug']}").get_data(as_text=True)
    assert '"ScholarlyArticle"' in html.replace(" ", "")


def test_doi_and_authors_visible_on_detail_page(seeded_lab, client):
    """DOI 與作者列必須對「人」可見，而不只存在於 JSON-LD（AC-17）。"""
    entry = PUBLICATIONS[0]
    html = client.get(f"/research/{entry['slug']}").get_data(as_text=True)
    body = re.search(r"<main.*?</main>", html, re.S).group(0)
    assert entry["doi"] in body
    assert entry["authors"].split(",")[0].strip() in body


def test_seed_is_idempotent(seeded_lab):
    """重複執行不得產生重複資料（以 DOI 判斷既存）。"""
    from app.extensions import db
    from app.models.research_output import ResearchOutput

    with seeded_lab.app_context():
        before = db.session.query(ResearchOutput).count()
        run_seed()
        db.session.commit()
        after = db.session.query(ResearchOutput).count()

    assert after == before == len(PUBLICATIONS)


def test_seed_requires_faculty(app):
    """沒有教授時必須明確中止並提示先跑 seed legacy。"""
    with app.app_context():
        with pytest.raises(RuntimeError, match="seed legacy"):
            run_seed()


def test_research_index_no_longer_empty(seeded_lab, client):
    """/research 不再是空頁（原本 research_outputs=0）。"""
    html = client.get("/research").get_data(as_text=True)
    assert "尚未發布" not in html
