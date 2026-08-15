# ============================================================
# NTUST SiPh Lab - Person Model / Service / Route Tests
#
# 上下游：
#   tests/conftest.py -> 本檔
#       -> services/person_service.py、models/person.py
#       -> blueprints/public（/members、/alumni、/people/<slug>）
#       -> blueprints/admin（新增 / 編輯 / 發布 / 轉為畢業生）
#
# 檔案路徑：tests/test_people.py
# 建立日期：2026-08-14 / 版本：v1.0
#
# 模組定位（SAI §20）：
#   涵蓋 Model（constraints / relationships）與
#   Route（public / admin）兩個測試層級。
#
# 對應驗收條目：
#   AC-04 新增 Current Member，發布後出現在 /members
#   AC-06 current 轉 alumni，person id/slug 不變，
#         /members 消失、/alumni 出現
#   AC-08 draft 不出現在 public（人物版本）
#   AC-12 有圖但無 alt 時，publish validator 阻擋
#   ADR-008 單一 Person 實體，成果關聯不斷裂
#
# 驗證方式：
#   pytest tests/test_people.py -v
# ============================================================

from __future__ import annotations

import pytest


# ----------------------------------------------------------------------
# AC-04：新增並發布成員後出現在 /members
# ----------------------------------------------------------------------
def test_ac04_published_member_appears_on_members_page(client, sample_person):
    """AC-04：發布後的在學成員出現在 /members。"""
    response = client.get("/members")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert sample_person["name_zh"] in html
    assert f'/people/{sample_person["slug"]}' in html


def test_ac04_admin_can_create_and_publish_member(app, logged_in_client):
    """AC-04 的後台路徑：透過 Admin 表單新增並發布。"""
    from app.models.mixins import PublishStatus
    from app.models.person import Person
    from app.extensions import db

    create = logged_in_client.post(
        "/admin/people/new",
        data={
            "name_zh": "王小明",
            "name_en": "Xiao-Ming Wang",
            "status": "current",
            "research_focus_zh": "矽光子調變器設計",
            "sort_order": "50",
        },
    )
    assert create.status_code == 302, "建立成功後應以 PRG 導向編輯頁"

    with app.app_context():
        person = db.session.scalar(db.select(Person).where(Person.name_zh == "王小明"))
        assert person is not None
        # 新建內容一律先是草稿（見 PersonService.create 的說明）。
        assert person.publish_status == PublishStatus.DRAFT
        person_id = person.id
        slug = person.slug

    # 草稿不得出現在前台（AC-08 的人物版本）。
    assert client_get_status(logged_in_client, f"/people/{slug}") == 404

    publish = logged_in_client.post(f"/admin/people/{person_id}/publish", data={})
    assert publish.status_code == 302

    # 發布後前台可見。
    assert client_get_status(logged_in_client, f"/people/{slug}") == 200
    assert "王小明" in logged_in_client.get("/members").get_data(as_text=True)


def client_get_status(client, path: str) -> int:
    """取得 GET 回應狀態碼的小工具（讓測試斷言更直觀）。"""
    return client.get(path).status_code


# ----------------------------------------------------------------------
# AC-08（人物）：draft / archived 不出現在前台
# ----------------------------------------------------------------------
def test_draft_person_is_not_public(client, draft_person):
    """草稿人物不出現在 /members，個人頁回 404。"""
    members_html = client.get("/members").get_data(as_text=True)
    assert draft_person["name_zh"] not in members_html

    assert client.get(f"/people/{draft_person['slug']}").status_code == 404


def test_archived_person_is_not_public(app, client, sample_person):
    """封存後的人物立即從前台消失。"""
    from app.extensions import db
    from app.models.person import Person
    from app.services.person_service import PersonService

    with app.app_context():
        person = db.session.get(Person, sample_person["id"])
        PersonService.archive(person)

    assert client.get(f"/people/{sample_person['slug']}").status_code == 404
    assert sample_person["name_zh"] not in client.get("/members").get_data(as_text=True)


# ----------------------------------------------------------------------
# AC-06：在學轉畢業（ADR-008 的核心保證）
# ----------------------------------------------------------------------
def test_ac06_graduate_preserves_id_and_slug(app, client, sample_person):
    """AC-06：轉為畢業生後 person id 與 slug 完全不變。"""
    from app.extensions import db
    from app.models.mixins import PersonStatus
    from app.models.person import Person
    from app.services.person_service import PersonService

    original_id = sample_person["id"]
    original_slug = sample_person["slug"]

    with app.app_context():
        person = db.session.get(Person, original_id)
        PersonService.graduate(person, graduation_year=2026, degree="M.S.")

        assert person.id == original_id, "graduate 不得改變 person id（ADR-008）"
        assert person.slug == original_slug, "graduate 不得改變 slug（AC-06）"
        assert person.status == PersonStatus.ALUMNI
        assert person.graduation_year == 2026


def test_ac06_graduate_moves_person_between_pages(app, client, sample_person):
    """AC-06：/members 消失、/alumni 出現，個人 URL 仍可用。"""
    from app.extensions import db
    from app.models.person import Person
    from app.services.person_service import PersonService

    # 轉換前：在 /members，不在 /alumni。
    assert sample_person["name_zh"] in client.get("/members").get_data(as_text=True)
    assert sample_person["name_zh"] not in client.get("/alumni").get_data(as_text=True)

    with app.app_context():
        person = db.session.get(Person, sample_person["id"])
        PersonService.graduate(person, graduation_year=2026, degree="M.S.")

    # 轉換後：從 /members 消失，出現在 /alumni。
    assert sample_person["name_zh"] not in client.get("/members").get_data(as_text=True)
    assert sample_person["name_zh"] in client.get("/alumni").get_data(as_text=True)

    # 個人 URL 不變且仍然有效（不需要 redirect）。
    assert client.get(f"/people/{sample_person['slug']}").status_code == 200


def test_ac06_graduate_preserves_research_output_links(app, sample_person, sample_output):
    """AC-06 / ADR-008：畢業後既有研究成果關聯完全保留。

    這是 ADR-008 選擇「單一 Person 實體」最重要的理由 ——
    若畢業時搬移資料，成果的作者關聯就會斷裂。
    """
    from app.extensions import db
    from app.models.person import Person
    from app.models.research_output import ResearchOutput
    from app.services.person_service import PersonService

    with app.app_context():
        person = db.session.get(Person, sample_person["id"])
        links_before = len(person.output_links)
        assert links_before == 1, "前置條件：sample_output 應已關聯此人"

        PersonService.graduate(person, graduation_year=2026)
        db.session.refresh(person)

        assert len(person.output_links) == links_before, "畢業不得影響成果關聯"

        # 從成果端也必須仍看得到這個人（雙向）。
        output = db.session.get(ResearchOutput, sample_output["id"])
        assert person.id in [p.id for p in output.lab_people]


def test_graduate_rejects_non_current_person(app, sample_person):
    """對非在學狀態執行畢業轉換必須被拒絕。"""
    from app.extensions import db
    from app.models.person import Person
    from app.services.person_service import PersonService, PersonServiceError

    with app.app_context():
        person = db.session.get(Person, sample_person["id"])
        PersonService.graduate(person, graduation_year=2026)

        # 第二次應被拒絕（已是 alumni）。
        with pytest.raises(PersonServiceError):
            PersonService.graduate(person, graduation_year=2027)


def test_graduate_writes_audit_log(app, sample_person):
    """畢業轉換必須寫入稽核紀錄（SAI §7.5 步驟 6）。"""
    from sqlalchemy import select

    from app.extensions import db
    from app.models.audit_log import AuditLog
    from app.models.mixins import AuditAction
    from app.models.person import Person
    from app.services.person_service import PersonService

    with app.app_context():
        person = db.session.get(Person, sample_person["id"])
        PersonService.graduate(person, graduation_year=2026)

        entries = db.session.scalars(
            select(AuditLog).where(AuditLog.action == AuditAction.GRADUATE)
        ).all()
        assert len(entries) == 1
        assert entries[0].entity_id == sample_person["id"]


# ----------------------------------------------------------------------
# AC-12：有圖無 alt 阻擋發布
# ----------------------------------------------------------------------
def test_ac12_photo_without_alt_blocks_publish(app, png_bytes):
    """AC-12：有照片但缺 alt 時，publish validator 阻擋發布。"""
    import io

    from werkzeug.datastructures import FileStorage

    from app.services.person_service import PersonService, PersonServiceError
    from app.services.publish_validator import PublishValidator

    with app.app_context():
        person = PersonService.create(
            {
                "name_zh": "缺 alt 測試",
                "status": "current",
                "research_focus_zh": "測試研究方向",
            }
        )

        # 上傳照片但「不」提供 alt。
        PersonService.attach_photo(
            person,
            FileStorage(stream=io.BytesIO(png_bytes), filename="p.png", content_type="image/png"),
            alt_zh=None,
        )

        result = PublishValidator.validate_person(person)
        assert not result.is_valid, "有圖無 alt 應阻擋發布"
        assert any("alt" in msg or "替代文字" in msg for msg in result.error_messages())

        with pytest.raises(PersonServiceError):
            PersonService.publish(person)


def test_ac12_photo_with_alt_allows_publish(app, png_bytes):
    """補上 alt 之後即可發布。"""
    import io

    from werkzeug.datastructures import FileStorage

    from app.models.mixins import PublishStatus
    from app.services.person_service import PersonService

    with app.app_context():
        person = PersonService.create(
            {
                "name_zh": "有 alt 測試",
                "status": "current",
                "research_focus_zh": "測試研究方向",
            }
        )
        PersonService.attach_photo(
            person,
            FileStorage(stream=io.BytesIO(png_bytes), filename="p.png", content_type="image/png"),
            alt_zh="測試人物照片",
        )

        PersonService.publish(person)
        assert person.publish_status == PublishStatus.PUBLISHED


# ----------------------------------------------------------------------
# 發布門檻（SAI §15.1）
# ----------------------------------------------------------------------
def test_publish_requires_research_focus(app):
    """一般人物缺研究焦點時不得發布。"""
    from app.services.person_service import PersonService, PersonServiceError

    with app.app_context():
        person = PersonService.create({"name_zh": "無焦點", "status": "current"})
        with pytest.raises(PersonServiceError):
            PersonService.publish(person)


def test_legacy_pending_person_may_publish_without_research_focus(app):
    """母站遷入且標記待補者可豁免研究焦點門檻。

    這是 2026-08-15 管理者裁示的實作（ADR-012）：
    四位碩二生的姓名必須可被查得（AC-23），
    但研究方向母站不存在，不得捏造（SAI §2.3）。
    豁免僅限研究焦點一項，且必須產生 warning 持續提醒。
    """
    from app.models.mixins import PublishStatus
    from app.services.person_service import PersonService
    from app.services.publish_validator import PublishValidator

    with app.app_context():
        person = PersonService.create(
            {"name_zh": "母站遷入成員", "status": "current", "legacy_pending_detail": True}
        )

        result = PublishValidator.validate_person(person)
        assert result.is_valid, "legacy 遷入人物應可發布"
        assert result.warnings, "但必須留下 warning 提醒補齊"

        PersonService.publish(person)
        assert person.publish_status == PublishStatus.PUBLISHED


def test_legacy_flag_clears_when_research_focus_filled(app):
    """補齊研究焦點後，待補標記自動解除（避免 Dashboard 永久噪音）。"""
    from app.extensions import db
    from app.models.person import Person
    from app.services.person_service import PersonService

    with app.app_context():
        person = PersonService.create(
            {"name_zh": "待補成員", "status": "current", "legacy_pending_detail": True}
        )
        assert person.legacy_pending_detail is True

        PersonService.update(
            person,
            {"name_zh": "待補成員", "status": "current", "research_focus_zh": "已補上的研究方向"},
        )
        db.session.refresh(person)
        assert person.legacy_pending_detail is False


def test_publish_rejects_invalid_external_url(app):
    """壞掉的外部連結阻擋發布（避免公開頁出現無效連結）。"""
    from app.services.publish_validator import PublishValidator
    from app.services.person_service import PersonService

    with app.app_context():
        person = PersonService.create(
            {
                "name_zh": "壞連結",
                "status": "current",
                "research_focus_zh": "測試",
            }
        )
        # 繞過 service 的正規化直接寫入壞值，模擬資料異常。
        person.github_url = "javascript:alert(1)"

        result = PublishValidator.validate_person(person)
        assert not result.is_valid


# ----------------------------------------------------------------------
# 隱私（SAI §5.4、§15.3）
# ----------------------------------------------------------------------
def test_destination_hidden_unless_explicitly_public(app, client, sample_person):
    """未勾選公開時，畢業去向不得出現在前台。"""
    from app.extensions import db
    from app.models.person import Person
    from app.services.person_service import PersonService

    with app.app_context():
        person = db.session.get(Person, sample_person["id"])
        PersonService.update(
            person,
            {
                "name_zh": person.name_zh,
                "status": "current",
                "research_focus_zh": person.research_focus_zh,
                "current_affiliation": "某某科技公司",
                "current_position": "研發工程師",
                "destination_public": False,
            },
        )
        assert person.public_destination is None

    html = client.get(f"/people/{sample_person['slug']}").get_data(as_text=True)
    assert "某某科技公司" not in html, "未確認可公開的就業資訊不得顯示（SAI §5.4）"


def test_destination_shown_when_marked_public(app, client, sample_person):
    """明確勾選公開後才顯示。"""
    from app.extensions import db
    from app.models.person import Person
    from app.services.person_service import PersonService

    with app.app_context():
        person = db.session.get(Person, sample_person["id"])
        PersonService.update(
            person,
            {
                "name_zh": person.name_zh,
                "status": "current",
                "research_focus_zh": person.research_focus_zh,
                "current_affiliation": "某某科技公司",
                "destination_public": True,
            },
        )

    html = client.get(f"/people/{sample_person['slug']}").get_data(as_text=True)
    assert "某某科技公司" in html


# ----------------------------------------------------------------------
# slug 行為
# ----------------------------------------------------------------------
def test_slug_is_unique_across_people(app):
    """同名人物會自動取得不同 slug。"""
    from app.services.person_service import PersonService

    with app.app_context():
        first = PersonService.create(
            {"name_zh": "陳大文", "status": "current", "research_focus_zh": "A"}
        )
        second = PersonService.create(
            {"name_zh": "陳大文", "status": "current", "research_focus_zh": "B"}
        )
        assert first.slug != second.slug
        assert second.slug.endswith("-2")


def test_slug_change_on_draft_creates_no_redirect(app):
    """草稿階段改 slug 不建立 redirect（該 URL 從未公開）。"""
    from sqlalchemy import func, select

    from app.extensions import db
    from app.models.redirect import Redirect
    from app.services.person_service import PersonService

    with app.app_context():
        person = PersonService.create(
            {"name_zh": "草稿改名", "status": "current", "research_focus_zh": "x"}
        )
        PersonService.update(
            person,
            {"name_zh": "草稿改名", "status": "current", "research_focus_zh": "x",
             "slug": "brand-new-slug"},
        )
        assert person.slug == "brand-new-slug"
        assert db.session.scalar(select(func.count(Redirect.id))) == 0


def test_slug_change_on_published_person_creates_redirect(app, client, sample_person):
    """已發布人物改 slug 會建立 301，舊網址仍可到達。"""
    from app.extensions import db
    from app.models.person import Person
    from app.services.person_service import PersonService

    old_slug = sample_person["slug"]

    with app.app_context():
        person = db.session.get(Person, sample_person["id"])
        PersonService.update(
            person,
            {
                "name_zh": person.name_zh,
                "status": "current",
                "research_focus_zh": person.research_focus_zh,
                "slug": "renamed-member",
            },
        )

    response = client.get(f"/people/{old_slug}")
    assert response.status_code == 301
    assert response.headers["Location"].endswith("/people/renamed-member")


# ----------------------------------------------------------------------
# 首頁與列表
# ----------------------------------------------------------------------
def test_alumni_page_groups_by_year(app, client):
    """畢業生依年度分組顯示。"""
    from app.models.mixins import PersonStatus
    from app.services.person_service import PersonService

    with app.app_context():
        for name, year in (("畢業甲", 2025), ("畢業乙", 2026)):
            person = PersonService.create(
                {
                    "name_zh": name,
                    "status": PersonStatus.ALUMNI,
                    "research_focus_zh": "研究方向",
                    "graduation_year": year,
                }
            )
            PersonService.publish(person)

    html = client.get("/alumni").get_data(as_text=True)
    assert "2025" in html and "2026" in html
    assert "畢業甲" in html and "畢業乙" in html
    # 新到舊：2026 應排在 2025 之前。
    assert html.index("2026") < html.index("2025")


def test_faculty_appears_on_about_page(app, client):
    """教授資料顯示在 /about。"""
    from app.models.mixins import PersonStatus
    from app.services.person_service import PersonService

    with app.app_context():
        person = PersonService.create(
            {
                "name_zh": "測試教授",
                "status": PersonStatus.FACULTY,
                "title_zh": "副教授",
                "education_zh": "某某大學博士",
                "research_focus_zh": "矽光子",
                "email_public": "prof@example.edu",
            }
        )
        PersonService.publish(person)

    html = client.get("/about").get_data(as_text=True)
    assert "測試教授" in html
    assert "副教授" in html
    assert "某某大學博士" in html
    assert "prof@example.edu" in html
