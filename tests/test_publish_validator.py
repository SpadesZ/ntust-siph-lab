# ============================================================
# NTUST SiPh Lab - Publish Validator Tests
#
# 檔案路徑：tests/test_publish_validator.py
# 建立日期：2026-08-15 / 版本：v1.0
#
# 模組定位（SAI §15 發布門檻、附錄 C Publish Checklist、AC-12）：
#   PublishValidator 是「什麼內容可以公開」的守門人。
#   它同時支撐 AC-12（有圖無 alt 必須阻擋）與
#   siph-lab-seo-geo Skill 的 publish gate
#   （空/薄內容、缺必要事實欄位不得發布）。
#
#   errors 會阻擋發布，warnings 只提醒。這個區分很重要：
#   過度阻擋會讓管理者無法漸進式編輯（SAI §7.2
#   「內容品質提醒，不阻擋草稿」）。
#
# 驗證方式：
#   pytest tests/test_publish_validator.py -v
# ============================================================

from __future__ import annotations

import pytest

from app.models.mixins import OutputType, PersonStatus
from app.models.person import Person
from app.models.research_output import ResearchOutput
from app.services.publish_validator import PublishValidator


def _person(**overrides) -> Person:
    data = {
        "slug": "test-person",
        "name_zh": "測試人物",
        "status": PersonStatus.CURRENT,
        "research_focus_zh": "矽光子微環諧振器設計",
    }
    data.update(overrides)
    return Person(**data)


def _output(**overrides) -> ResearchOutput:
    data = {
        "slug": "test-output",
        "output_type": OutputType.JOURNAL,
        "year": 2026,
        "title_zh": "測試成果",
        "summary_zh": "本研究提出以矽光子微環諧振器實現光通道效能監視的方法，"
                      "並以模擬與實測驗證其可行性與量測誤差範圍。",
    }
    data.update(overrides)
    return ResearchOutput(**data)


# ----------------------------------------------------------------------
# Person
# ----------------------------------------------------------------------
def test_valid_person_passes(app):
    with app.app_context():
        assert PublishValidator.validate_person(_person()).is_valid


def test_person_without_name_blocked(app):
    with app.app_context():
        result = PublishValidator.validate_person(_person(name_zh=None))
    assert not result.is_valid


def test_person_without_research_focus_blocked(app):
    """SAI §15.1：research focus 是 published 的門檻。"""
    with app.app_context():
        result = PublishValidator.validate_person(
            _person(research_focus_zh=None, research_focus_en=None)
        )
    assert not result.is_valid
    assert any("研究" in m for m in result.error_messages())


def test_person_research_focus_in_english_only_is_acceptable(app):
    """SAI §15.1：至少一語言有值即可。"""
    with app.app_context():
        result = PublishValidator.validate_person(
            _person(research_focus_zh=None, research_focus_en="Silicon photonics")
        )
    assert result.is_valid


@pytest.mark.acceptance
def test_ac12_photo_without_alt_blocked(app):
    """AC-12：有圖但無 alt 時必須阻擋發布。"""
    with app.app_context():
        result = PublishValidator.validate_person(
            _person(photo_path="people/abc.jpg", photo_alt_zh=None, photo_alt_en=None)
        )
    assert not result.is_valid
    assert any("alt" in m.lower() or "替代" in m for m in result.error_messages())


def test_photo_with_alt_passes(app):
    with app.app_context():
        result = PublishValidator.validate_person(
            _person(photo_path="people/abc.jpg", photo_alt_zh="測試人物的照片")
        )
    assert result.is_valid


def test_person_without_photo_is_fine(app):
    """SAI §15.1：照片 optional。"""
    with app.app_context():
        assert PublishValidator.validate_person(_person(photo_path=None)).is_valid


def test_alumni_without_graduation_year_flagged(app):
    """SAI §8.3：alumni 建議必填 graduation_year。"""
    with app.app_context():
        result = PublishValidator.validate_person(
            _person(status=PersonStatus.ALUMNI, graduation_year=None)
        )
    assert result.errors or result.warnings, "畢業生缺畢業年度應至少提醒"


def test_invalid_external_url_flagged(app):
    with app.app_context():
        result = PublishValidator.validate_person(_person(orcid_url="not a url"))
    assert result.errors or result.warnings


# ----------------------------------------------------------------------
# ResearchOutput
# ----------------------------------------------------------------------
def test_valid_output_passes(app):
    with app.app_context():
        assert PublishValidator.validate_research(_output()).is_valid


def test_output_without_title_blocked(app):
    with app.app_context():
        result = PublishValidator.validate_research(_output(title_zh=None, title_en=None))
    assert not result.is_valid


def test_output_without_summary_blocked(app):
    """SAI §15.2：至少一語言 summary 是發布門檻。"""
    with app.app_context():
        result = PublishValidator.validate_research(_output(summary_zh=None, summary_en=None))
    assert not result.is_valid


def test_output_without_year_blocked(app):
    with app.app_context():
        result = PublishValidator.validate_research(_output(year=None))
    assert not result.is_valid


@pytest.mark.acceptance
def test_ac12_hero_image_without_alt_blocked(app):
    with app.app_context():
        result = PublishValidator.validate_research(
            _output(hero_image_path="research/x.jpg", hero_image_alt_zh=None)
        )
    assert not result.is_valid


def test_journal_without_method_or_results_warns(app):
    """SAI §15.2：論文建議至少 Method + Results。

    這是 warning 而非 error —— 過度阻擋會妨礙漸進式編輯。
    """
    with app.app_context():
        result = PublishValidator.validate_research(
            _output(method_zh=None, results_zh=None)
        )
    assert result.is_valid, "缺方法/結果不應阻擋發布"
    assert result.warnings, "但應該要提醒"


def test_malformed_doi_flagged(app):
    with app.app_context():
        result = PublishValidator.validate_research(_output(doi="這不是 DOI"))
    assert result.errors or result.warnings


def test_valid_doi_accepted(app):
    """正規化後的裸 DOI（小寫）應通過。"""
    with app.app_context():
        result = PublishValidator.validate_research(_output(doi="10.1109/jlt.2026.1234567"))
    assert result.is_valid


def test_unnormalised_doi_flagged(app):
    """未正規化的 DOI（大寫或含 URL 前綴）應被攔下。

    若原樣存入，同一篇論文會產生多種值，
    重複偵測與 JSON-LD identifier 都會失準（SAI §15.2）。
    """
    with app.app_context():
        for raw in ("10.1109/JLT.2026.1234567", "https://doi.org/10.1109/jlt.2026.1"):
            result = PublishValidator.validate_research(_output(doi=raw))
            assert not result.is_valid, f"未正規化的 DOI 應被攔下：{raw}"


# ----------------------------------------------------------------------
# ValidationResult 行為
# ----------------------------------------------------------------------
def test_warnings_do_not_invalidate(app):
    from app.services.publish_validator import ValidationResult

    result = ValidationResult()
    result.add_warning("field", "只是提醒")
    assert result.is_valid
    assert result.warning_messages() == ["只是提醒"]


def test_errors_invalidate(app):
    from app.services.publish_validator import ValidationResult

    result = ValidationResult()
    result.add_error("field", "阻擋原因")
    assert not result.is_valid
    assert result.error_messages() == ["阻擋原因"]
