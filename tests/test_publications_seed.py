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


def test_summaries_contain_no_unverified_metrics():
    """摘要不得出現未經查證的效能數值（SAI §2.3、§6.3）。

    只有標題本身載明的數值才允許出現。這條測試防止日後有人
    「順手」把看起來合理的效能數字補進摘要。
    """
    metric_pattern = re.compile(r"\d+(\.\d+)?\s*(dB|dBm|%|GHz|nm|Gbps)", re.IGNORECASE)
    for entry in PUBLICATIONS:
        found = metric_pattern.findall(entry["summary_zh"])
        assert not found, (
            f"{entry['slug']} 的摘要含未查證數值 {found}；"
            "摘要只能改寫標題與出處所陳述的事實"
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


def test_bidirectional_link_with_professor(seeded_lab, client):
    """SAI §13.1 Cross-entity linking：Person <-> ResearchOutput 雙向可導航。"""
    person = client.get("/people/chun-liang-yang").get_data(as_text=True)
    for entry in PUBLICATIONS:
        assert entry["slug"] in person, f"教授頁未列出 {entry['slug']}"

    detail = client.get(f"/research/{PUBLICATIONS[0]['slug']}").get_data(as_text=True)
    assert "/people/chun-liang-yang" in detail


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
