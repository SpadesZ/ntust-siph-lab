# ============================================================
# NTUST SiPh Lab - Admin Form Validation Tests
#
# 檔案路徑：tests/test_admin_forms.py
# 建立日期：2026-08-15 / 版本：v1.0
#
# 模組定位（SAI §9.1 Form/Validator 層、§15 欄位級規格）：
#   表單是「使用者看得到錯誤訊息」的那一層。service 層雖然也會
#   把非法值正規化為 None，但那是靜默的 —— 管理者打錯 email
#   後只會發現欄位變空白，不知道發生什麼事。
#
#   因此表單必須自己攔下並回報。本檔驗證這一層真的有在擋，
#   而不是全部依賴 service 的靜默清理。
#
# 責任邊界提醒（SAI §9.1）：
#   Form 只做欄位驗證，不得直接 commit DB。
#
# 驗證方式：
#   pytest tests/test_admin_forms.py -v
# ============================================================

from __future__ import annotations

import pytest

from app.blueprints.admin.forms import PersonForm, ResearchForm


def _bind(app, form_class, data: dict):
    """在 request context 中建立並驗證表單。"""
    with app.test_request_context(method="POST", data=data):
        form = form_class()
        form.validate()
        return form


def _person_data(**overrides) -> dict:
    data = {
        "name_zh": "表單測試",
        "slug": "form-test",
        "status": "current",
        "research_focus_zh": "矽光子元件設計",
        "sort_order": "100",
    }
    data.update(overrides)
    return data


def _output_data(**overrides) -> dict:
    data = {
        "output_type": "journal",
        "year": "2026",
        "title_zh": "表單測試成果",
        "slug": "form-test-output",
        "summary_zh": "本研究以矽光子微環諧振器驗證光通道效能監視方法的可行性與量測誤差。",
        "sort_order": "100",
    }
    data.update(overrides)
    return data


# ----------------------------------------------------------------------
# PersonForm
# ----------------------------------------------------------------------
def test_valid_person_form_passes(app):
    form = _bind(app, PersonForm, _person_data())
    assert not form.errors, form.errors


def test_person_form_requires_name(app):
    form = _bind(app, PersonForm, _person_data(name_zh=""))
    assert "name_zh" in form.errors


@pytest.mark.parametrize("status", ["", "unknown", "superuser", "faculty; DROP TABLE"])
def test_person_form_rejects_invalid_status(app, status):
    """status 有 CheckConstraint，表單必須先擋，否則會變成 DB 錯誤畫面。"""
    form = _bind(app, PersonForm, _person_data(status=status))
    assert "status" in form.errors


@pytest.mark.parametrize("field,value", [
    ("orcid_url", "not a url"),
    ("scholar_url", "javascript:alert(1)"),
    ("github_url", "data:text/html,<script>"),
    ("linkedin_url", "ftp://example.com"),
])
def test_person_form_rejects_bad_urls(app, field, value):
    """SAI §15.1：Links 欄位必須做格式檢查。

    javascript: 與 data: 若寫入 href，點擊即觸發 XSS。
    """
    form = _bind(app, PersonForm, _person_data(**{field: value}))
    assert field in form.errors, f"{field}={value!r} 應被攔下"


def test_person_form_rejects_bad_email(app):
    form = _bind(app, PersonForm, _person_data(email_public="not-an-email"))
    assert "email_public" in form.errors


def test_person_form_accepts_valid_links(app):
    form = _bind(app, PersonForm, _person_data(
        orcid_url="https://orcid.org/0000-0002-1825-0097",
        scholar_url="https://scholar.google.com/citations?user=abc",
        github_url="https://github.com/example",
        email_public="yangcl@mail.ntust.edu.tw",
    ))
    for field in ("orcid_url", "scholar_url", "github_url", "email_public"):
        assert field not in form.errors, form.errors


@pytest.mark.parametrize("year", ["1800", "3000", "abc", "-5"])
def test_person_form_rejects_out_of_range_year(app, year):
    form = _bind(app, PersonForm, _person_data(entry_year=year))
    assert "entry_year" in form.errors


def test_person_form_accepts_blank_optional_fields(app):
    """SAI §15.1：Links 全部 optional，留空不得報錯。"""
    form = _bind(app, PersonForm, _person_data(
        orcid_url="", scholar_url="", github_url="", linkedin_url="", email_public="",
        entry_year="", graduation_year="",
    ))
    for field in ("orcid_url", "scholar_url", "github_url", "linkedin_url",
                  "email_public", "entry_year", "graduation_year"):
        assert field not in form.errors, form.errors


def test_person_form_rejects_negative_sort_order(app):
    form = _bind(app, PersonForm, _person_data(sort_order="-1"))
    assert "sort_order" in form.errors


# ----------------------------------------------------------------------
# ResearchForm
# ----------------------------------------------------------------------
def test_valid_output_form_passes(app):
    form = _bind(app, ResearchForm, _output_data())
    assert not form.errors, form.errors


def test_output_form_requires_year(app):
    form = _bind(app, ResearchForm, _output_data(year=""))
    assert "year" in form.errors


@pytest.mark.parametrize("output_type", ["", "unknown", "article"])
def test_output_form_rejects_invalid_type(app, output_type):
    form = _bind(app, ResearchForm, _output_data(output_type=output_type))
    assert "output_type" in form.errors


@pytest.mark.parametrize("output_type", [
    "journal", "conference", "project", "prototype", "simulation", "dataset", "other",
])
def test_output_form_accepts_all_sai_types(app, output_type):
    """SAI §8.4 列出的 output_type 全部必須可用。"""
    form = _bind(app, ResearchForm, _output_data(output_type=output_type))
    assert "output_type" not in form.errors, form.errors


@pytest.mark.parametrize("url_field", ["external_url", "github_url", "dataset_url"])
def test_output_form_rejects_bad_urls(app, url_field):
    form = _bind(app, ResearchForm, _output_data(**{url_field: "javascript:alert(1)"}))
    assert url_field in form.errors


def test_output_form_accepts_various_doi_forms(app):
    """SAI §15.2：DOI 應接受多種輸入形式並正規化，而非要求使用者手動清理。"""
    for raw in ("10.1109/jlt.2026.1234567",
                "https://doi.org/10.1109/jlt.2026.1234567",
                "doi:10.1109/jlt.2026.1234567"):
        form = _bind(app, ResearchForm, _output_data(doi=raw))
        assert "doi" not in form.errors, f"{raw!r} 應可接受：{form.errors}"


def test_output_form_rejects_nonsense_doi(app):
    form = _bind(app, ResearchForm, _output_data(doi="這不是 DOI"))
    assert "doi" in form.errors


def test_output_form_year_range(app):
    assert "year" in _bind(app, ResearchForm, _output_data(year="1800")).errors
    assert "year" in _bind(app, ResearchForm, _output_data(year="3000")).errors


# ----------------------------------------------------------------------
# 責任邊界（SAI §9.1：Form 不得直接 commit DB）
# ----------------------------------------------------------------------
def test_forms_do_not_commit_database():
    """表單模組不得出現 db.session.commit()。

    SAI §9.1 明定 Form/Validator 層「不要直接 commit DB」。
    若表單自行 commit，route 的錯誤處理與 PRG 流程就失去控制權。
    """
    import ast
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent
              / "app" / "blueprints" / "admin" / "forms.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "commit"
    ]
    assert not offenders, f"forms.py 不得 commit DB（行 {offenders}）"
