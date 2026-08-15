# ============================================================
# NTUST SiPh Lab - Research Output Tests
#
# 上下游：
#   tests/conftest.py -> 本檔
#       -> services/research_service.py、models/research_output.py
#       -> blueprints/public（/research、/research/<slug>）
#       -> models/redirect.py（slug 變更 301）
#
# 檔案路徑：tests/test_research.py
# 建立日期：2026-08-14 / 版本：v1.0
#
# 對應驗收條目：
#   AC-07 新增 ResearchOutput，關聯 Person 後雙向可導航
#   AC-08 draft 不出現在 public / sitemap
#   AC-09 published 出現在 /research 與 sitemap
#   AC-10 修改 published slug 產生 301 redirect
#   AC-12 有圖無 alt 阻擋發布（成果版本）
#
# 驗證方式：
#   pytest tests/test_research.py -v
# ============================================================

from __future__ import annotations

import pytest


# ----------------------------------------------------------------------
# AC-07：Person <-> ResearchOutput 雙向導航
# ----------------------------------------------------------------------
def test_ac07_bidirectional_navigation(client, sample_person, sample_output):
    """AC-07：成果頁可連到人物頁，人物頁可連到成果頁。"""
    # 成果頁 -> 人物頁
    output_html = client.get(f"/research/{sample_output['slug']}").get_data(as_text=True)
    assert f'/people/{sample_person["slug"]}' in output_html
    assert sample_person["name_zh"] in output_html

    # 人物頁 -> 成果頁
    person_html = client.get(f"/people/{sample_person['slug']}").get_data(as_text=True)
    assert f'/research/{sample_output["slug"]}' in person_html
    assert sample_output["title"] in person_html


def test_ac07_admin_can_link_people_to_output(app, sample_person):
    """關聯必須指向真實存在的人物，否則拒絕。"""
    from app.models.mixins import OutputType
    from app.services.research_service import ResearchService, ResearchServiceError

    with app.app_context():
        with pytest.raises(ResearchServiceError):
            ResearchService.create(
                {
                    "output_type": OutputType.PROJECT,
                    "year": 2026,
                    "title_zh": "不存在的關聯",
                    "summary_zh": "測試摘要內容足夠長以通過發布門檻檢查。",
                    "people": [999999],  # 不存在的 person id
                }
            )


def test_sync_people_replaces_previous_links(app, sample_person, sample_output):
    """人物關聯採全量替換語意。"""
    from app.extensions import db
    from app.models.person import Person
    from app.models.research_output import ResearchOutput
    from app.services.person_service import PersonService
    from app.services.research_service import ResearchService

    with app.app_context():
        other = PersonService.create(
            {"name_zh": "另一位成員", "status": "current", "research_focus_zh": "其他方向"}
        )
        output = db.session.get(ResearchOutput, sample_output["id"])

        ResearchService.sync_people(output, [other.id])
        db.session.commit()
        db.session.refresh(output)

        linked_ids = [p.id for p in output.lab_people]
        assert linked_ids == [other.id]
        assert sample_person["id"] not in linked_ids


def test_duplicate_person_link_is_deduplicated(app, sample_person, sample_output):
    """同一人被重複送出時只建立一筆關聯（UNIQUE 約束的前置處理）。"""
    from app.extensions import db
    from app.models.research_output import ResearchOutput
    from app.services.research_service import ResearchService

    with app.app_context():
        output = db.session.get(ResearchOutput, sample_output["id"])
        ResearchService.sync_people(
            output, [sample_person["id"], sample_person["id"], sample_person["id"]]
        )
        db.session.commit()
        db.session.refresh(output)
        assert len(output.person_links) == 1


# ----------------------------------------------------------------------
# AC-08 / AC-09：發布狀態與可見性
# ----------------------------------------------------------------------
def test_ac09_published_output_appears_in_index_and_sitemap(client, sample_output):
    """AC-09：published 成果出現在 /research 與 sitemap。"""
    index_html = client.get("/research").get_data(as_text=True)
    assert sample_output["title"] in index_html
    assert f'/research/{sample_output["slug"]}' in index_html

    sitemap = client.get("/sitemap.xml").get_data(as_text=True)
    assert f'/research/{sample_output["slug"]}' in sitemap


def test_ac08_draft_output_excluded_from_public_and_sitemap(app, client, sample_output):
    """AC-08：draft 成果不出現在 public 也不進 sitemap。"""
    from app.extensions import db
    from app.models.research_output import ResearchOutput
    from app.services.research_service import ResearchService

    with app.app_context():
        output = db.session.get(ResearchOutput, sample_output["id"])
        ResearchService.unpublish(output)

    assert client.get(f"/research/{sample_output['slug']}").status_code == 404
    assert sample_output["title"] not in client.get("/research").get_data(as_text=True)
    assert f'/research/{sample_output["slug"]}' not in client.get("/sitemap.xml").get_data(as_text=True)


def test_archived_output_excluded_from_public(app, client, sample_output):
    """archived 成果同樣不出現在前台。"""
    from app.extensions import db
    from app.models.research_output import ResearchOutput
    from app.services.research_service import ResearchService

    with app.app_context():
        output = db.session.get(ResearchOutput, sample_output["id"])
        ResearchService.archive(output)

    assert client.get(f"/research/{sample_output['slug']}").status_code == 404


def test_unpublish_clears_featured_flag(app, sample_output):
    """退回草稿時必須同時取消精選（未發布內容不得為精選）。"""
    from app.extensions import db
    from app.models.research_output import ResearchOutput
    from app.services.research_service import ResearchService

    with app.app_context():
        output = db.session.get(ResearchOutput, sample_output["id"])
        ResearchService.set_featured(output, True)
        assert output.is_featured is True

        ResearchService.unpublish(output)
        assert output.is_featured is False


def test_featured_requires_published(app, sample_output):
    """SAI §15.2：只有已發布的成果才能設為精選。"""
    from app.extensions import db
    from app.models.research_output import ResearchOutput
    from app.services.research_service import ResearchService, ResearchServiceError

    with app.app_context():
        output = db.session.get(ResearchOutput, sample_output["id"])
        ResearchService.unpublish(output)

        with pytest.raises(ResearchServiceError):
            ResearchService.set_featured(output, True)


# ----------------------------------------------------------------------
# AC-10：slug 變更產生 301
# ----------------------------------------------------------------------
def test_ac10_slug_change_creates_redirect(app, client, sample_output):
    """AC-10：修改已發布成果的 slug 會建立 301 redirect。"""
    from app.extensions import db
    from app.models.research_output import ResearchOutput
    from app.services.research_service import ResearchService

    old_slug = sample_output["slug"]

    with app.app_context():
        output = db.session.get(ResearchOutput, sample_output["id"])
        ResearchService.update(
            output,
            {
                "output_type": output.output_type,
                "year": output.year,
                "title_zh": output.title_zh,
                "summary_zh": output.summary_zh,
                "slug": "new-research-slug",
            },
        )

    response = client.get(f"/research/{old_slug}")
    assert response.status_code == 301, "舊網址必須 301 而非 404（AC-10）"
    assert response.headers["Location"].endswith("/research/new-research-slug")

    # 新網址正常可用。
    assert client.get("/research/new-research-slug").status_code == 200


def test_redirect_chain_is_flattened(app, client, sample_output):
    """連續兩次改 slug 時，最舊的網址仍只需一次 301。

    為什麼重要：多跳 301 會浪費 crawl budget，
    部分爬蟲在多次轉址後會放棄。
    """
    from app.extensions import db
    from app.models.research_output import ResearchOutput
    from app.services.research_service import ResearchService

    original = sample_output["slug"]

    with app.app_context():
        output = db.session.get(ResearchOutput, sample_output["id"])
        base = {
            "output_type": output.output_type,
            "year": output.year,
            "title_zh": output.title_zh,
            "summary_zh": output.summary_zh,
        }
        ResearchService.update(output, {**base, "slug": "second-slug"})
        ResearchService.update(output, {**base, "slug": "third-slug"})

    response = client.get(f"/research/{original}")
    assert response.status_code == 301
    assert response.headers["Location"].endswith("/research/third-slug"), (
        "鏈式轉址必須壓平為單次 301"
    )


def test_redirect_never_targets_admin(app):
    """禁止建立指向後台的 redirect（安全防護）。"""
    from app.extensions import db
    from app.models.redirect import Redirect

    with app.app_context():
        record = Redirect.record("/research/old", "/admin/people")
        assert record is None
        db.session.rollback()


def test_redirect_ignores_self_reference(app):
    """來源與目標相同時不建立 redirect（避免無限迴圈）。"""
    from app.models.redirect import Redirect

    with app.app_context():
        assert Redirect.record("/research/same", "/research/same") is None
        assert Redirect.record("/research/same/", "/research/same") is None


# ----------------------------------------------------------------------
# 發布門檻（SAI §15.2）
# ----------------------------------------------------------------------
def test_publish_requires_summary(app):
    """缺摘要不得發布（成果頁是 SEO/GEO 最重要的內容單位）。"""
    from app.models.mixins import OutputType
    from app.services.research_service import ResearchService, ResearchServiceError

    with app.app_context():
        output = ResearchService.create(
            {"output_type": OutputType.PROJECT, "year": 2026, "title_zh": "無摘要成果"}
        )
        with pytest.raises(ResearchServiceError):
            ResearchService.publish(output)


def test_publish_requires_title(app):
    """標題至少需一個語言版本。"""
    from app.models.mixins import OutputType
    from app.services.research_service import ResearchService, ResearchServiceError

    with app.app_context():
        with pytest.raises(ResearchServiceError):
            ResearchService.create(
                {"output_type": OutputType.PROJECT, "year": 2026, "summary_zh": "只有摘要"}
            )


def test_ac12_hero_image_without_alt_blocks_publish(app, sample_output, png_bytes):
    """AC-12（成果版本）：有主圖但缺 alt 時阻擋發布。"""
    import io

    from werkzeug.datastructures import FileStorage

    from app.extensions import db
    from app.models.research_output import ResearchOutput
    from app.services.publish_validator import PublishValidator
    from app.services.research_service import ResearchService

    with app.app_context():
        output = db.session.get(ResearchOutput, sample_output["id"])
        ResearchService.attach_hero_image(
            output,
            FileStorage(stream=io.BytesIO(png_bytes), filename="h.png", content_type="image/png"),
            alt_zh=None,
        )
        # 清掉可能沿用的 alt，模擬「只上傳圖沒填說明」。
        output.hero_image_alt_zh = None
        db.session.commit()

        result = PublishValidator.validate_research(output)
        assert not result.is_valid


# ----------------------------------------------------------------------
# DOI 正規化（SAI §15.2）
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("10.1109/JLT.2026.1234567", "10.1109/jlt.2026.1234567"),
        ("https://doi.org/10.1109/JLT.2026.1234567", "10.1109/jlt.2026.1234567"),
        ("doi:10.1109/JLT.2026.1234567", "10.1109/jlt.2026.1234567"),
        ("  10.1109/JLT.2026.1234567  ", "10.1109/jlt.2026.1234567"),
    ],
)
def test_doi_normalization(app, raw, expected):
    """各種 DOI 輸入形式都正規化為同一個裸 DOI。"""
    from app.models.research_output import ResearchOutput

    assert ResearchOutput.normalize_doi(raw) == expected


def test_invalid_doi_is_rejected_with_message(app):
    """無法解析的 DOI 必須明確報錯，而不是靜默丟棄。

    靜默丟棄會讓管理者以為已存檔，之後才發現 DOI 消失。
    """
    from app.models.mixins import OutputType
    from app.services.research_service import ResearchService, ResearchServiceError

    with app.app_context():
        with pytest.raises(ResearchServiceError) as exc:
            ResearchService.create(
                {
                    "output_type": OutputType.JOURNAL,
                    "year": 2026,
                    "title_zh": "壞 DOI",
                    "summary_zh": "測試摘要",
                    "doi": "not-a-doi-at-all",
                }
            )
        assert "DOI" in str(exc.value)


def test_empty_doi_is_allowed(app):
    """沒有 DOI 是合法狀態（不是所有成果都有 DOI）。"""
    from app.models.mixins import OutputType
    from app.services.research_service import ResearchService

    with app.app_context():
        output = ResearchService.create(
            {
                "output_type": OutputType.PROJECT,
                "year": 2026,
                "title_zh": "無 DOI 專案",
                "summary_zh": "測試摘要",
                "doi": "",
            }
        )
        assert output.doi is None


# ----------------------------------------------------------------------
# 篩選
# ----------------------------------------------------------------------
def test_research_index_filters_by_type_and_year(app, client, sample_output):
    """/research 的 type 與 year 篩選。"""
    from app.models.mixins import OutputType
    from app.services.research_service import ResearchService

    with app.app_context():
        other = ResearchService.create(
            {
                "output_type": OutputType.PROTOTYPE,
                "year": 2024,
                "title_zh": "原型系統測試",
                "summary_zh": "另一筆成果的摘要內容。",
            }
        )
        ResearchService.publish(other)

    # 依類型篩選。
    journal_html = client.get("/research?type=journal").get_data(as_text=True)
    assert sample_output["title"] in journal_html
    assert "原型系統測試" not in journal_html

    # 依年份篩選。
    year_html = client.get("/research?year=2024").get_data(as_text=True)
    assert "原型系統測試" in year_html
    assert sample_output["title"] not in year_html


def test_invalid_filter_values_are_ignored(client, sample_output):
    """不合法的篩選值被忽略而非產生空頁面。"""
    html = client.get("/research?type=nonsense&year=abcd").get_data(as_text=True)
    assert sample_output["title"] in html


def test_filters_do_not_change_canonical(client, sample_output):
    """SAI §4.2：篩選參數不得產生新的 canonical。"""
    import re

    filtered = client.get("/research?type=journal&year=2026").get_data(as_text=True)
    canonical = re.search(r'<link rel="canonical" href="([^"]+)"', filtered).group(1)

    assert canonical.endswith("/research"), (
        f"篩選後的 canonical 不得包含 query 參數，實際為 {canonical}"
    )


# ----------------------------------------------------------------------
# 顯示邏輯
# ----------------------------------------------------------------------
def test_scholarly_type_detection(app):
    """journal / conference 為學術論文；其餘不是。"""
    from app.models.research_output import ResearchOutput
    from app.models.mixins import OutputType

    for output_type, expected in (
        (OutputType.JOURNAL, True),
        (OutputType.CONFERENCE, True),
        (OutputType.PROJECT, False),
        (OutputType.PROTOTYPE, False),
        (OutputType.DATASET, False),
    ):
        output = ResearchOutput(slug="x", year=2026, output_type=output_type)
        assert output.is_scholarly is expected


def test_keywords_are_deduplicated_and_trimmed(app):
    """關鍵字去重與 trim（SAI §15.2）。"""
    from app.models.research_output import ResearchOutput

    output = ResearchOutput(slug="x", year=2026)
    output.keywords = ["  矽光子 ", "矽光子", "光通訊", "光通訊  "]
    assert output.keywords == ["矽光子", "光通訊"]


def test_corrupt_keywords_json_returns_empty_list(app):
    """損毀的 JSON 不得讓頁面崩潰。"""
    from app.models.research_output import ResearchOutput

    output = ResearchOutput(slug="x", year=2026)
    output.keywords_json = "{not valid json"
    assert output.keywords == []
