# ============================================================
# NTUST SiPh Lab - 後台維護功能
#
# 上下游：
#   ResearchService.duplicate -> routes::research_duplicate
#   _macros.status_filter_bar -> people_list / research_list
#   routes::alumni_list（搜尋與年度篩選）
#       -> 本測試
#
# 檔案路徑：
#   tests/test_admin_maintenance.py
#
# 建立日期：2026-09-02
# 版本：v1.0
#
# 模組定位與責任邊界：
#   驗證「實際維護情境」需要但原本缺少的功能：
#     1. 以既有成果為範本建立新的（同期刊連發數篇）
#     2. 已封存項目的可見入口（封存後不該像是資料不見了）
#     3. 畢業生的搜尋與年度篩選（會逐年累積）
#
#   責任邊界（不得做的事）：
#     - 不驗證版面樣式，只驗證行為與可達性。
#
# 主要 Function：
#   test_duplicate_creates_draft_copy
#   test_duplicate_does_not_copy_identity_fields
#   test_duplicate_keeps_author_order
#   test_list_pages_expose_archived_filter
#   test_alumni_can_be_filtered_by_year
#   test_alumni_can_be_searched
#   test_alumni_empty_result_offers_to_clear_filter
#
# 依賴套件：pytest, beautifulsoup4
#
# 驗證方式：
#   pytest tests/test_admin_maintenance.py
# ============================================================

from __future__ import annotations

import pytest


def _soup(html: str):
    from bs4 import BeautifulSoup

    return BeautifulSoup(html, "html.parser")


# ----------------------------------------------------------------------
# 以既有成果為範本建立新的（B17）
# ----------------------------------------------------------------------
def test_duplicate_creates_draft_copy(logged_in_client, app, sample_output):
    """複製會產生一筆新的草稿，且不影響原始資料。"""
    from app.extensions import db
    from app.models.research_output import ResearchOutput

    response = logged_in_client.post(
        f"/admin/research/{sample_output['id']}/duplicate", follow_redirects=False
    )
    assert response.status_code == 302

    with app.app_context():
        outputs = db.session.scalars(db.select(ResearchOutput)).all()
        assert len(outputs) == 2, "應該多出一筆"

        source = db.session.get(ResearchOutput, sample_output["id"])
        copy = next(o for o in outputs if o.id != source.id)

        assert copy.publish_status == "draft", "複製出來的必須是草稿"
        assert copy.slug != source.slug, "slug 不可共用"
        assert copy.venue == source.venue, "共通欄位應該沿用"
        assert source.publish_status == "published", "原始資料不得被更動"


def test_duplicate_does_not_copy_identity_fields(logged_in_client, app, sample_output):
    """DOI 與主圖不得複製。

    DOI 是該篇論文的唯一識別碼，複製會產生兩筆指向同一篇文獻的
    資料（SAI §14.3 禁止不實出版資訊）；主圖若共用同一個
    object key，移除其中一筆的圖會把另一筆的圖也刪掉。
    """
    from app.extensions import db
    from app.models.research_output import ResearchOutput

    logged_in_client.post(f"/admin/research/{sample_output['id']}/duplicate")

    with app.app_context():
        source = db.session.get(ResearchOutput, sample_output["id"])
        assert source.doi, "前提：原始資料要有 DOI 才驗得出來"

        copy = db.session.scalars(
            db.select(ResearchOutput).where(ResearchOutput.id != source.id)
        ).first()

        assert copy.doi is None, "DOI 不得複製"
        assert copy.hero_image_path is None, "主圖不得複製（object key 會被共用）"
        assert copy.is_featured is False, "精選狀態不得複製"


def test_duplicate_keeps_author_order(logged_in_client, app, sample_output):
    """作者與其順序要一併帶過來。"""
    from app.extensions import db
    from app.models.research_output import ResearchOutput

    logged_in_client.post(f"/admin/research/{sample_output['id']}/duplicate")

    with app.app_context():
        source = db.session.get(ResearchOutput, sample_output["id"])
        copy = db.session.scalars(
            db.select(ResearchOutput).where(ResearchOutput.id != source.id)
        ).first()

        assert [p.name_zh for p in copy.lab_people] == [
            p.name_zh for p in source.lab_people
        ]


# ----------------------------------------------------------------------
# 已封存項目的可見入口（B19）
# ----------------------------------------------------------------------
@pytest.mark.parametrize("path", ["/admin/people", "/admin/research"])
def test_list_pages_expose_archived_filter(logged_in_client, path):
    """列表頁必須有「已封存」的可點入口。

    封存後的項目預設不顯示，側選單也沒有入口 ——
    實測時封存一筆資料後第一直覺是「資料不見了」。
    """
    soup = _soup(logged_in_client.get(path).get_data(as_text=True))
    links = soup.select('nav[aria-label="發布狀態快速篩選"] a')

    assert links, f"{path} 缺少發布狀態快速篩選"

    archived = [a for a in links if "已封存" in a.get_text()]
    assert archived, f"{path} 沒有『已封存』入口"
    assert "publish_status=archived" in archived[0]["href"]


# ----------------------------------------------------------------------
# 畢業生搜尋與篩選（B20）
# ----------------------------------------------------------------------
@pytest.fixture()
def two_alumni(app):
    """兩位不同年度的畢業生。"""
    from app.services.person_service import PersonService

    with app.app_context():
        for name, year, affiliation in [
            ("畢業生甲", 2024, "台積電"),
            ("畢業生乙", 2025, "聯發科"),
        ]:
            person = PersonService.create(
                {"name_zh": name, "status": "current", "research_focus_zh": "x"}
            )
            PersonService.graduate(person, graduation_year=year)
            PersonService.update(
                person,
                {
                    "name_zh": name,
                    "status": "alumni",
                    "graduation_year": year,
                    "current_affiliation": affiliation,
                },
            )
        return True


def test_alumni_can_be_filtered_by_year(logged_in_client, two_alumni):
    """依畢業年度篩選只顯示該年度。"""
    html = logged_in_client.get("/admin/alumni?year=2024").get_data(as_text=True)

    assert "畢業生甲" in html
    assert "畢業生乙" not in html


def test_alumni_can_be_searched(logged_in_client, two_alumni):
    """可以用姓名或目前單位搜尋。"""
    by_name = logged_in_client.get("/admin/alumni?q=畢業生乙").get_data(as_text=True)
    assert "畢業生乙" in by_name and "畢業生甲" not in by_name

    by_affiliation = logged_in_client.get("/admin/alumni?q=台積電").get_data(as_text=True)
    assert "畢業生甲" in by_affiliation and "畢業生乙" not in by_affiliation


def test_alumni_empty_result_offers_to_clear_filter(logged_in_client, two_alumni):
    """篩選後沒有結果時，訊息要與「本來就沒資料」區分開。"""
    html = logged_in_client.get("/admin/alumni?q=不存在的人").get_data(as_text=True)

    assert "沒有符合條件" in html, "應說明是篩選造成的空結果"
    assert "清除篩選" in html, "應提供清除篩選的出口"
