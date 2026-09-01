# ============================================================
# NTUST SiPh Lab - 研究成果作者順序
#
# 上下游：
#   research_form.html（author_order-<id> 輸入）
#       -> ResearchForm.parse_author_orders
#       -> routes::_research_payload -> ResearchService.sync_people
#       -> ResearchOutput.public_lab_people（前台顯示順序）
#
# 檔案路徑：
#   tests/test_research_author_order.py
#
# 建立日期：2026-09-02
# 版本：v1.0
#
# 模組定位與責任邊界：
#   原本關聯成員用 SelectMultipleField，而多選清單送出的順序是
#   「選項在 DOM 中的順序」，也就是人物的 sort_order，
#   與管理者的點選順序無關。sync_people 又以清單順序寫入
#   sort_order，前台 public_lab_people 照它顯示 ——
#   結果「論文的作者順序 = 這些人在成員頁的排序值」，
#   且在成果頁完全無法調整。
#
#   對學術網站而言第一作者順序不能錯，因此本檔守住：
#     1. 順序由管理者指定，與成員清單的排列無關
#     2. 留空 = 不列入
#     3. 非法輸入要回報而不是靜默忽略
#
# 主要 Function：
#   test_author_order_overrides_member_list_position
#   test_blank_order_excludes_person
#   test_invalid_order_is_reported
#   test_duplicate_order_is_reported
#   test_edit_page_prefills_existing_order
#
# 依賴套件：pytest
#
# 資料庫使用方式：透過 conftest 的暫存 SQLite。
#
# 已知限制與禁止事項：
#   1. 禁止改回以「清單順序」決定作者順序。
#
# 驗證方式：
#   pytest tests/test_research_author_order.py
# ============================================================

from __future__ import annotations

import pytest


@pytest.fixture()
def three_people(app):
    """建立三位成員，並讓「教授」的 sort_order 排在最後。

    這正是真實資料的樣子（實測：楊淳良在選單中排最後），
    也是原本缺陷最容易顯現的情境 —— 若順序由清單位置決定，
    教授永遠只能是最後一位作者。
    """
    from app.services.person_service import PersonService

    with app.app_context():
        made = {}
        for key, name, status, order in [
            ("student_a", "學生甲", "current", 1),
            ("student_b", "學生乙", "current", 2),
            ("professor", "指導教授", "faculty", 99),
        ]:
            person = PersonService.create(
                {
                    "name_zh": name,
                    "status": status,
                    "research_focus_zh": "測試研究方向",
                    "sort_order": order,
                }
            )
            made[key] = person.id
        return made


def _output_form_data(**overrides) -> dict:
    data = {
        "output_type": "journal",
        "year": "2026",
        "title_zh": "作者順序測試成果",
    }
    data.update(overrides)
    return data


def _author_names(app, output_id) -> list[str]:
    from app.extensions import db
    from app.models.research_output import ResearchOutput

    with app.app_context():
        output = db.session.get(ResearchOutput, output_id)
        return [p.name_zh for p in output.lab_people]


def test_author_order_overrides_member_list_position(logged_in_client, app, three_people):
    """指定教授為第一作者時，實際順序就必須是教授在前。

    教授的 sort_order 是 99（成員清單最後一位）。
    若順序仍由清單位置決定，這個測試會失敗。
    """
    from app.extensions import db
    from app.models.research_output import ResearchOutput

    response = logged_in_client.post(
        "/admin/research/new",
        data=_output_form_data(
            **{
                f"author_order-{three_people['professor']}": "1",
                f"author_order-{three_people['student_a']}": "2",
                f"author_order-{three_people['student_b']}": "3",
            }
        ),
        follow_redirects=False,
    )
    assert response.status_code == 302, "建立成果應成功並轉址"

    with app.app_context():
        output_id = db.session.scalars(db.select(ResearchOutput.id)).first()

    assert _author_names(app, output_id) == ["指導教授", "學生甲", "學生乙"], (
        "作者順序沒有依照管理者指定的數字，仍受成員清單排列影響"
    )


def test_author_order_can_be_reversed(logged_in_client, app, three_people):
    """把順序反過來填，結果也要跟著反過來（證明真的讀了輸入值）。"""
    from app.extensions import db
    from app.models.research_output import ResearchOutput

    logged_in_client.post(
        "/admin/research/new",
        data=_output_form_data(
            **{
                f"author_order-{three_people['professor']}": "3",
                f"author_order-{three_people['student_a']}": "2",
                f"author_order-{three_people['student_b']}": "1",
            }
        ),
    )

    with app.app_context():
        output_id = db.session.scalars(db.select(ResearchOutput.id)).first()

    assert _author_names(app, output_id) == ["學生乙", "學生甲", "指導教授"]


def test_blank_order_excludes_person(logged_in_client, app, three_people):
    """留空的成員不列入這筆成果。"""
    from app.extensions import db
    from app.models.research_output import ResearchOutput

    logged_in_client.post(
        "/admin/research/new",
        data=_output_form_data(
            **{
                f"author_order-{three_people['professor']}": "1",
                f"author_order-{three_people['student_a']}": "",
                f"author_order-{three_people['student_b']}": "2",
            }
        ),
    )

    with app.app_context():
        output_id = db.session.scalars(db.select(ResearchOutput.id)).first()

    names = _author_names(app, output_id)
    assert names == ["指導教授", "學生乙"]
    assert "學生甲" not in names


@pytest.mark.parametrize("bad", ["abc", "0", "-1"])
def test_invalid_order_is_reported(app, three_people, bad):
    """非法順序值要回報，不得靜默忽略。"""
    from werkzeug.datastructures import MultiDict

    from app.blueprints.admin.forms import ResearchForm

    with app.app_context():
        form_data = MultiDict({f"author_order-{three_people['professor']}": bad})
        entries, errors = ResearchForm.parse_author_orders(
            form_data, list(three_people.values())
        )

        assert entries == [], "非法值不該被當成有效順序"
        assert errors, f"「{bad}」應該產生錯誤訊息而不是被默默丟掉"


def test_duplicate_order_is_reported(app, three_people):
    """兩位成員填相同順序時要提醒（結果仍需穩定可預期）。"""
    from werkzeug.datastructures import MultiDict

    from app.blueprints.admin.forms import ResearchForm

    with app.app_context():
        form_data = MultiDict(
            {
                f"author_order-{three_people['professor']}": "1",
                f"author_order-{three_people['student_a']}": "1",
            }
        )
        entries, errors = ResearchForm.parse_author_orders(
            form_data, list(three_people.values())
        )

        assert len(entries) == 2, "重複順序仍應保留兩位成員"
        assert any("相同的順序" in e for e in errors)


def test_edit_page_prefills_existing_order(logged_in_client, app, three_people):
    """編輯頁要把既有的作者順序填回輸入框。"""
    from app.extensions import db
    from app.models.research_output import ResearchOutput

    logged_in_client.post(
        "/admin/research/new",
        data=_output_form_data(
            **{
                f"author_order-{three_people['professor']}": "1",
                f"author_order-{three_people['student_b']}": "2",
            }
        ),
    )

    with app.app_context():
        output_id = db.session.scalars(db.select(ResearchOutput.id)).first()

    html = logged_in_client.get(f"/admin/research/{output_id}/edit").get_data(as_text=True)

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    prof_input = soup.find("input", attrs={"name": f"author_order-{three_people['professor']}"})
    stub_input = soup.find("input", attrs={"name": f"author_order-{three_people['student_a']}"})

    assert prof_input.get("value") == "1", "既有的第一作者順序應該回填"
    assert not stub_input.get("value"), "未列入的成員應維持留空"
