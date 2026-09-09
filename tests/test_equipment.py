# ============================================================
# NTUST SiPh Lab - 研究設備功能測試
#
# 檔案路徑：tests/test_equipment.py
# 建立日期：2026-09-09 / 版本：v1.0
#
# 涵蓋範圍：
#   1. model 與 CheckConstraint
#   2. repository 的公開過濾與分組
#   3. 發布門檻（PublishValidator.validate_equipment）
#   4. 公開頁渲染、empty state、SEO、sitemap
#   5. 導覽第 7 項
#
# 本檔最重要的兩組測試：
#   - test_draft_equipment_is_not_public：draft 絕不外流。整份設備
#     資料目前都是 draft，這條若失效，未經教授確認的設備會直接
#     出現在官網上。
#   - test_non_lab_equipment_requires_source：非自有設備必須有出處。
#     這是「不在官網放無法查證的機台」這個承諾的技術防線。
# ============================================================

from __future__ import annotations

import pytest

from app.extensions import db
from app.models.equipment import Equipment
from app.models.mixins import EquipmentCategory, EquipmentOwnership, PublishStatus
from app.repositories import equipment as equipment_repo
from app.services.publish_validator import PublishValidator


def _make(**kwargs) -> Equipment:
    """建立一筆設備，預設值為「可通過發布門檻的自有設備」。"""
    data = {
        "slug": "test-device",
        "name_zh": "測試設備",
        "category": EquipmentCategory.MEASUREMENT,
        "ownership": EquipmentOwnership.LAB,
        "description_zh": "用途說明。",
        "publish_status": PublishStatus.DRAFT,
    }
    data.update(kwargs)
    item = Equipment(**data)
    db.session.add(item)
    db.session.commit()
    return item


# ------------------------------------------------------------------
# model
# ------------------------------------------------------------------

def test_equipment_defaults_to_draft(app):
    with app.app_context():
        item = _make(slug="defaults")
        assert item.publish_status == PublishStatus.DRAFT
        assert item.sort_order == 100
        assert item.is_featured is False


def test_slug_must_be_unique(app):
    with app.app_context():
        _make(slug="dup")
        with pytest.raises(Exception):
            _make(slug="dup")
        db.session.rollback()


@pytest.mark.parametrize("field,value", [
    ("category", "not-a-category"),
    ("ownership", "not-an-ownership"),
    ("publish_status", "not-a-status"),
])
def test_check_constraints_reject_bad_values(app, field, value):
    """CheckConstraint 必須在 DB 層擋下非法值。

    這些常數同時存在於 mixins 與 migration 的 CheckConstraint，
    兩邊漂移時就會出現「後台可選但 DB 拒絕」。
    """
    with app.app_context():
        with pytest.raises(Exception):
            _make(slug=f"bad-{field}", **{field: value})
        db.session.rollback()


# ------------------------------------------------------------------
# repository
# ------------------------------------------------------------------

def test_draft_equipment_is_not_public(app):
    """draft 不得出現在任何公開查詢結果中。"""
    with app.app_context():
        _make(slug="hidden", publish_status=PublishStatus.DRAFT)
        assert equipment_repo.list_published() == []
        assert equipment_repo.list_published_grouped() == []
        assert equipment_repo.get_by_slug("hidden") is None
        # 後台仍看得到。
        assert len(equipment_repo.admin_list()) == 1


def test_grouped_orders_by_display_order_and_skips_empty(app):
    """分組依 DISPLAY_ORDER，且空的分組不出現。"""
    with app.app_context():
        _make(
            slug="shared-one",
            ownership=EquipmentOwnership.SHARED,
            source_note="來源",
            publish_status=PublishStatus.PUBLISHED,
        )
        _make(
            slug="institute-one",
            ownership=EquipmentOwnership.INSTITUTE,
            source_note="來源",
            publish_status=PublishStatus.PUBLISHED,
        )

        grouped = equipment_repo.list_published_grouped()
        assert [ownership for ownership, _ in grouped] == [
            EquipmentOwnership.INSTITUTE,
            EquipmentOwnership.SHARED,
        ]
        # lab 沒有資料，該分組不應出現（否則頁面會有空標題）。
        assert EquipmentOwnership.LAB not in [o for o, _ in grouped]


def test_grouped_respects_sort_order(app):
    with app.app_context():
        _make(slug="second", sort_order=20, publish_status=PublishStatus.PUBLISHED)
        _make(slug="first", sort_order=10, publish_status=PublishStatus.PUBLISHED)
        _, items = equipment_repo.list_published_grouped()[0]
        assert [i.slug for i in items] == ["first", "second"]


# ------------------------------------------------------------------
# 發布門檻
# ------------------------------------------------------------------

def test_non_lab_equipment_requires_source(app):
    """非本實驗室設備缺出處時不得發布。

    讀者無法分辨頁面上哪一台是實驗室真的有、哪一台是從別處抄來的；
    出處是他們唯一能查證的依據，因此這是 error 而非 warning。
    """
    with app.app_context():
        item = _make(slug="no-source", ownership=EquipmentOwnership.INSTITUTE)
        result = PublishValidator.validate_equipment(item)
        assert not result.is_valid
        assert any("出處" in msg for msg in result.error_messages())


def test_non_lab_equipment_passes_with_source_url_only(app):
    """出處給連結或說明其中之一即可。"""
    with app.app_context():
        item = _make(
            slug="has-url",
            ownership=EquipmentOwnership.INSTITUTE,
            source_url="https://www.ntust.edu.tw/",
        )
        assert PublishValidator.validate_equipment(item).is_valid


def test_lab_equipment_does_not_require_source(app):
    """自有設備由教授直接提供，本人即出處。"""
    with app.app_context():
        item = _make(slug="own-device", ownership=EquipmentOwnership.LAB)
        assert PublishValidator.validate_equipment(item).is_valid


def test_photo_requires_alt(app):
    """有照片就必須有 alt（比照 AC-12）。"""
    with app.app_context():
        item = _make(slug="with-photo", photo_path="equipment/x.jpg")
        result = PublishValidator.validate_equipment(item)
        assert not result.is_valid
        assert any("alt" in msg or "替代文字" in msg for msg in result.error_messages())


def test_name_is_required(app):
    with app.app_context():
        item = _make(slug="no-name", name_zh=None, name_en=None)
        assert not PublishValidator.validate_equipment(item).is_valid


# ------------------------------------------------------------------
# 公開頁
# ------------------------------------------------------------------

def test_equipment_page_renders_empty_state(client):
    """沒有已發布設備時顯示 empty state，而不是空白或假內容。"""
    response = client.get("/equipment")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "設備資訊整理中" in body


def test_equipment_page_shows_group_headings_and_source(app, client):
    with app.app_context():
        _make(
            slug="visible-device",
            name_zh="可見設備",
            ownership=EquipmentOwnership.INSTITUTE,
            source_note="臺科大官網新聞稿",
            source_url="https://www.ntust.edu.tw/",
            location_zh="華夏校區",
            publish_status=PublishStatus.PUBLISHED,
        )

    body = client.get("/equipment").get_data(as_text=True)
    assert "可見設備" in body
    assert "所屬中心共用設施" in body
    # 分組說明必須出現：它是「可以用到」與「我們擁有」的差別所在。
    assert "設置於所屬單位" in body
    assert "臺科大官網新聞稿" in body
    assert "華夏校區" in body
    # 沒有 lab 資料時不得出現該分組標題。
    assert "本實驗室設備" not in body


def test_equipment_page_has_single_h1(client):
    """每頁唯一 h1（AC-18）。"""
    body = client.get("/equipment").get_data(as_text=True)
    assert body.count("<h1") == 1


def test_equipment_in_sitemap(client):
    body = client.get("/sitemap.xml").get_data(as_text=True)
    assert "/equipment" in body


def test_equipment_nav_link_present(client):
    """導覽必須有第 7 項，且指向 /equipment。"""
    body = client.get("/").get_data(as_text=True)
    assert 'href="/equipment"' in body
    assert "研究設備" in body


def test_equipment_page_canonical_and_description(client):
    """canonical 指向自己；沒有資料時 description 不承諾數量。"""
    body = client.get("/equipment").get_data(as_text=True)
    assert 'rel="canonical"' in body
    assert "/equipment" in body
    # 無已發布項目時不得出現「共 N 項」這種承諾。
    assert "共 0 項" not in body


# ------------------------------------------------------------------
# 後台 CRUD
# ------------------------------------------------------------------

def test_admin_equipment_pages_require_login(client):
    """未登入不得存取後台設備頁（AC-01）。"""
    for path in ("/admin/equipment", "/admin/equipment/new"):
        response = client.get(path)
        assert response.status_code == 302
        assert "/admin/login" in response.headers["Location"]


def test_admin_can_create_equipment(logged_in_client, app):
    response = logged_in_client.post(
        "/admin/equipment/new",
        data={
            "name_zh": "新設備",
            "ownership": EquipmentOwnership.LAB,
            "category": EquipmentCategory.MEASUREMENT,
            "description_zh": "用途。",
            "sort_order": 10,
        },
        follow_redirects=True,
    )
    assert response.status_code == 200

    with app.app_context():
        item = db.session.query(Equipment).filter_by(name_zh="新設備").first()
        assert item is not None
        # 新建一律是草稿，不得直接上線。
        assert item.publish_status == PublishStatus.DRAFT
        assert item.slug


def test_admin_publish_blocked_without_source(logged_in_client, app):
    """非自有設備缺出處時，發布必須被擋下且資料維持草稿。

    這是「不在官網放無法查證的機台」在 HTTP 層的最後一道防線。
    """
    with app.app_context():
        item = _make(
            slug="blocked", ownership=EquipmentOwnership.INSTITUTE, source_note=None
        )
        item_id = item.id

    response = logged_in_client.post(
        f"/admin/equipment/{item_id}/publish", data={}, follow_redirects=True
    )
    assert response.status_code == 200
    assert "出處" in response.get_data(as_text=True)

    with app.app_context():
        assert db.session.get(Equipment, item_id).publish_status == PublishStatus.DRAFT


def test_admin_publish_succeeds_with_source(logged_in_client, app):
    with app.app_context():
        item = _make(
            slug="publishable",
            ownership=EquipmentOwnership.INSTITUTE,
            source_note="臺科大官網新聞稿",
        )
        item_id = item.id

    logged_in_client.post(
        f"/admin/equipment/{item_id}/publish", data={}, follow_redirects=True
    )

    with app.app_context():
        assert (
            db.session.get(Equipment, item_id).publish_status
            == PublishStatus.PUBLISHED
        )


def test_admin_unpublish_clears_featured(logged_in_client, app):
    """退回草稿時必須一併取消精選，否則下次發布會悄悄生效。"""
    with app.app_context():
        item = _make(
            slug="featured-one",
            publish_status=PublishStatus.PUBLISHED,
            is_featured=True,
        )
        item_id = item.id

    logged_in_client.post(
        f"/admin/equipment/{item_id}/unpublish", data={}, follow_redirects=True
    )

    with app.app_context():
        refreshed = db.session.get(Equipment, item_id)
        assert refreshed.publish_status == PublishStatus.DRAFT
        assert refreshed.is_featured is False


def test_admin_list_renders(logged_in_client, app):
    with app.app_context():
        _make(slug="listed", name_zh="列表設備")
    body = logged_in_client.get("/admin/equipment").get_data(as_text=True)
    assert "列表設備" in body
    assert "研究設備" in body
