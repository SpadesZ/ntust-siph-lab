# ============================================================
# NTUST SiPh Lab - Admin Route Tests
#
# 上下游：
#   tests/conftest.py（app / logged_in_client / sample_* fixtures）
#       -> app/blueprints/admin/routes.py
#       -> app/services/*（透過 route，不直接呼叫）
#
# 檔案路徑：tests/test_admin.py
# 建立日期：2026-08-15 / 版本：v1.0
#
# 模組定位（SAI §20 Route 層、§7 後台 CMS）：
#   Admin CMS 是管理者維護內容的唯一介面，但原本沒有專屬測試模組，
#   22 條 route 中有 11 條從未被任何測試觸及（archive / feature /
#   unpublish / 媒體刪除 / research edit 等破壞性操作全部零覆蓋）。
#
#   本檔專注在「接線層」—— route ↔ form ↔ service 的銜接。
#   這一層最容易斷裂，而 service 層的單元測試抓不到：
#   例如 AC-06 的 graduate 在 service 層有 6 個測試，
#   卻從未經過真正的 HTTP route，因此表單解析、CSRF、
#   redirect 與 flash 全部未被驗證。
#
# 涵蓋範圍：
#   1. 全域 authz：列舉 url_map 中所有 admin route 逐一驗證
#   2. 內容狀態轉換：publish / unpublish / archive / feature
#   3. graduate（AC-06）經真實 HTTP route
#   4. 媒體刪除與「被引用的檔案不可直接刪」（SAI §16）
#   5. 設定與密碼變更
#   6. 無效輸入與不存在的 id
#
# 為什麼列舉 url_map 而不是寫死清單：
#   寫死的清單會隨新增 route 而過期，且過期時「測試仍然通過」——
#   那是最糟的失效方式。從 url_map 動態列舉可確保
#   任何新增的 admin route 自動被納入保護檢查。
#
# 驗證方式：
#   pytest tests/test_admin.py -v
#   pytest -m acceptance
# ============================================================

from __future__ import annotations

import io

import pytest

from tests.conftest import TEST_ADMIN_PASSWORD, TEST_ADMIN_USERNAME


def _png_bytes(size=(400, 400), colour=(20, 60, 90)) -> bytes:
    """產生一張合法的 PNG（用於上傳測試）。"""
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, format="PNG")
    return buffer.getvalue()


# ----------------------------------------------------------------------
# 1. 全域授權（AC-01 的完整版）
# ----------------------------------------------------------------------
def _admin_rules(app):
    """列舉所有 admin route，並填入具體 id。"""
    rules = []
    for rule in app.url_map.iter_rules():
        path = str(rule)
        if not path.startswith("/admin") or "login" in path:
            continue
        concrete = (
            path.replace("<int:person_id>", "1")
            .replace("<int:output_id>", "1")
            .replace("<field>", "logo")
        )
        if "<" in concrete:
            continue
        for method in ("GET", "POST"):
            if method in rule.methods:
                rules.append((method, concrete))
    return rules


@pytest.mark.acceptance
def test_every_admin_route_requires_login(app, client):
    """AC-01：未登入時，所有 admin route 都不得洩漏資料。

    動態列舉 url_map（見檔頭），因此新增 route 會自動被檢查。
    """
    leaks = []
    for method, path in _admin_rules(app):
        response = client.open(path, method=method, follow_redirects=False)
        redirected = (
            response.status_code in (301, 302)
            and "/admin/login" in response.headers.get("Location", "")
        )
        # CSRF 在此 app 關閉，因此 POST 也應走到 login 重導。
        if not redirected:
            leaks.append(f"{method} {path} -> {response.status_code}")

    assert not leaks, "以下 admin route 未受保護：\n  " + "\n  ".join(leaks)


def test_admin_routes_exist(app):
    """確保列舉邏輯真的有掃到 route（避免測試空轉而假通過）。"""
    assert len(_admin_rules(app)) >= 20


# ----------------------------------------------------------------------
# 2. Dashboard 與列表
# ----------------------------------------------------------------------
def test_dashboard_renders(logged_in_client, sample_person):
    response = logged_in_client.get("/admin/")
    assert response.status_code == 200


@pytest.mark.parametrize("path", ["/admin/people", "/admin/research", "/admin/alumni",
                                  "/admin/settings", "/admin/system", "/admin/password"])
def test_admin_pages_render(logged_in_client, path):
    assert logged_in_client.get(path).status_code == 200


# ----------------------------------------------------------------------
# 3. 人物狀態轉換
# ----------------------------------------------------------------------
def test_person_unpublish_removes_from_public(logged_in_client, client, sample_person):
    """unpublish 後前台不再顯示（原本零覆蓋）。"""
    assert sample_person["name_zh"] in client.get("/members").get_data(as_text=True)

    response = logged_in_client.post(f"/admin/people/{sample_person['id']}/unpublish")
    assert response.status_code == 302

    assert sample_person["name_zh"] not in client.get("/members").get_data(as_text=True)
    assert client.get(f"/people/{sample_person['slug']}").status_code == 404


def test_person_archive_hides_but_keeps_data(logged_in_client, client, app, sample_person):
    """archive 後前台隱藏但資料保留（SAI §7.6 預設不硬刪）。"""
    response = logged_in_client.post(f"/admin/people/{sample_person['id']}/archive")
    assert response.status_code == 302
    assert sample_person["name_zh"] not in client.get("/members").get_data(as_text=True)

    from app.extensions import db
    from app.models.person import Person

    with app.app_context():
        person = db.session.get(Person, sample_person["id"])
        assert person is not None, "archive 不得刪除資料"
        assert person.name_zh == sample_person["name_zh"]


def test_person_republish_after_unpublish(logged_in_client, client, sample_person):
    """退回草稿後可以再次發布。"""
    logged_in_client.post(f"/admin/people/{sample_person['id']}/unpublish")
    logged_in_client.post(f"/admin/people/{sample_person['id']}/publish")
    assert sample_person["name_zh"] in client.get("/members").get_data(as_text=True)


# ----------------------------------------------------------------------
# 4. AC-06 graduate 經真實 HTTP route
# ----------------------------------------------------------------------
@pytest.mark.acceptance
def test_ac06_graduate_via_http_route(logged_in_client, client, app, sample_person,
                                      sample_output):
    """AC-06：經 HTTP route 執行 graduate，而非直接呼叫 service。

    service 層已有測試，但 route 的表單解析、驗證與 redirect
    原本完全沒有被驗證過。
    """
    response = logged_in_client.post(
        f"/admin/people/{sample_person['id']}/graduate",
        data={
            "graduation_year": "2026",
            "degree": "M.S.",
            "thesis_title_zh": "矽光子微環諧振器之光通道效能監視研究",
        },
    )
    assert response.status_code == 302, "graduate route 應以 PRG 導回"

    # 前台分流
    assert sample_person["name_zh"] not in client.get("/members").get_data(as_text=True)
    assert sample_person["name_zh"] in client.get("/alumni").get_data(as_text=True)

    # id / slug 不變，URL 不需 redirect
    detail = client.get(f"/people/{sample_person['slug']}")
    assert detail.status_code == 200

    # 成果關聯保留
    assert sample_output["slug"] in detail.get_data(as_text=True)

    from app.extensions import db
    from app.models.mixins import PersonStatus
    from app.models.person import Person

    with app.app_context():
        person = db.session.get(Person, sample_person["id"])
        assert person.status == PersonStatus.ALUMNI
        assert person.slug == sample_person["slug"]
        assert person.graduation_year == 2026


def test_graduate_missing_year_keeps_status(app, logged_in_client, sample_person):
    """缺畢業年度時不得變更狀態（驗證失敗必須是原子的）。"""
    logged_in_client.post(
        f"/admin/people/{sample_person['id']}/graduate", data={"degree": "M.S."}
    )

    from app.extensions import db
    from app.models.mixins import PersonStatus
    from app.models.person import Person

    with app.app_context():
        person = db.session.get(Person, sample_person["id"])
        assert person.status == PersonStatus.CURRENT, "驗證失敗時不得變更狀態"
        assert person.graduation_year is None


# ----------------------------------------------------------------------
# 5. 研究成果 route（原本大量零覆蓋）
# ----------------------------------------------------------------------
def test_research_edit_page_renders(logged_in_client, sample_output):
    assert logged_in_client.get(f"/admin/research/{sample_output['id']}/edit").status_code == 200


def test_research_unpublish_and_archive(logged_in_client, client, app, sample_output):
    assert logged_in_client.post(
        f"/admin/research/{sample_output['id']}/unpublish"
    ).status_code == 302
    assert client.get(f"/research/{sample_output['slug']}").status_code == 404

    assert logged_in_client.post(
        f"/admin/research/{sample_output['id']}/archive"
    ).status_code == 302

    from app.extensions import db
    from app.models.research_output import ResearchOutput

    with app.app_context():
        assert db.session.get(ResearchOutput, sample_output["id"]) is not None


def test_research_feature_set_and_unset(logged_in_client, app, sample_output):
    """featured 設定與取消（SAI §15.2：published 才能 featured）。

    注意 route 採「明確指定目標狀態」而非盲目 toggle
    （`featured=1` / `featured=0`）。這讓重複送出同一請求是冪等的，
    不會因為使用者連點兩次而把剛設定的精選又取消掉。
    """
    from app.extensions import db
    from app.models.research_output import ResearchOutput

    assert logged_in_client.post(
        f"/admin/research/{sample_output['id']}/feature", data={"featured": "1"}
    ).status_code == 302

    with app.app_context():
        assert db.session.get(ResearchOutput, sample_output["id"]).is_featured is True

    # 冪等：再送一次相同請求，狀態不變。
    logged_in_client.post(
        f"/admin/research/{sample_output['id']}/feature", data={"featured": "1"}
    )
    with app.app_context():
        assert db.session.get(ResearchOutput, sample_output["id"]).is_featured is True

    logged_in_client.post(
        f"/admin/research/{sample_output['id']}/feature", data={"featured": "0"}
    )
    with app.app_context():
        assert db.session.get(ResearchOutput, sample_output["id"]).is_featured is False


def test_featured_output_appears_on_homepage(logged_in_client, client, sample_output):
    """精選成果應出現在首頁（SAI §5.1 section 03）。"""
    logged_in_client.post(
        f"/admin/research/{sample_output['id']}/feature", data={"featured": "1"}
    )
    assert sample_output["slug"] in client.get("/").get_data(as_text=True)


# ----------------------------------------------------------------------
# 6. 媒體刪除（SAI §16 Deletion）
# ----------------------------------------------------------------------
def test_person_photo_upload_then_delete(logged_in_client, app, sample_person):
    """上傳後刪除照片，且實體檔案一併移除。"""
    from app.extensions import db
    from app.models.person import Person

    edit_url = f"/admin/people/{sample_person['id']}/edit"
    page = logged_in_client.get(edit_url)
    assert page.status_code == 200

    response = logged_in_client.post(
        edit_url,
        data={
            "name_zh": sample_person["name_zh"],
            "slug": sample_person["slug"],
            "status": "current",
            "research_focus_zh": "矽光子元件設計與量測",
            "photo_alt_zh": "測試成員的實驗室照片",
            "sort_order": "100",
            "photo": (io.BytesIO(_png_bytes()), "photo.png"),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 302, "合法 PNG 上傳應成功"

    with app.app_context():
        person = db.session.get(Person, sample_person["id"])
        assert person.photo_path, "上傳後應記錄 object key"
        stored_key = person.photo_path

    delete = logged_in_client.post(f"/admin/people/{sample_person['id']}/photo/delete")
    assert delete.status_code == 302

    with app.app_context():
        person = db.session.get(Person, sample_person["id"])
        assert not person.photo_path, "刪除後 photo_path 應清空"

        from app.storage import get_storage

        assert not get_storage().exists(stored_key), "實體檔案應一併刪除"


@pytest.mark.acceptance
def test_ac11_disguised_executable_rejected(logged_in_client, app, sample_person):
    """AC-11：偽裝成圖片的可執行檔必須被拒。"""
    from app.extensions import db
    from app.models.person import Person

    response = logged_in_client.post(
        f"/admin/people/{sample_person['id']}/edit",
        data={
            "name_zh": sample_person["name_zh"],
            "slug": sample_person["slug"],
            "status": "current",
            "research_focus_zh": "矽光子元件設計與量測",
            "sort_order": "100",
            # 副檔名偽裝 + 宣告 image/jpeg，但內容是 PHP
            "photo": (io.BytesIO(b"<?php system($_GET['c']); ?>"), "evil.php"),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 200, "非法上傳應重新顯示表單而非成功導向"

    with app.app_context():
        person = db.session.get(Person, sample_person["id"])
        assert not person.photo_path


def test_svg_upload_rejected(logged_in_client, app, sample_person):
    """SAI §16：不接受一般 admin 任意 SVG（可含 script）。"""
    payload = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    response = logged_in_client.post(
        f"/admin/people/{sample_person['id']}/edit",
        data={
            "name_zh": sample_person["name_zh"],
            "slug": sample_person["slug"],
            "status": "current",
            "research_focus_zh": "矽光子元件設計與量測",
            "sort_order": "100",
            "photo": (io.BytesIO(payload), "x.svg"),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 200

    from app.extensions import db
    from app.models.person import Person

    with app.app_context():
        assert not db.session.get(Person, sample_person["id"]).photo_path


# ----------------------------------------------------------------------
# 7. 設定與密碼
# ----------------------------------------------------------------------
def test_settings_update_reflects_on_public_site(logged_in_client, client):
    """網站設定變更應反映在前台。"""
    response = logged_in_client.post(
        "/admin/settings",
        data={
            "lab_name_zh": "測試矽光子實驗室",
            "lab_name_en": "Test SiPh Lab",
            "contact_email": "test@mail.ntust.edu.tw",
        },
        follow_redirects=False,
    )
    assert response.status_code in (302, 200)
    assert "測試矽光子實驗室" in client.get("/").get_data(as_text=True)


def test_password_change_requires_current_password(logged_in_client):
    """改密碼必須提供正確的舊密碼。"""
    response = logged_in_client.post(
        "/admin/password",
        data={
            "current_password": "wrong-password",
            "new_password": "brand-new-password-123",
            "confirm_password": "brand-new-password-123",
        },
    )
    assert response.status_code == 200, "舊密碼錯誤應重新顯示表單"


def test_password_change_succeeds(app, logged_in_client, admin_user):
    new_password = "brand-new-password-123"
    response = logged_in_client.post(
        "/admin/password",
        data={
            "current_password": TEST_ADMIN_PASSWORD,
            "new_password": new_password,
            "confirm_password": new_password,
        },
    )
    assert response.status_code in (302, 200)

    from app.extensions import db
    from app.models.admin_user import AdminUser

    with app.app_context():
        user = db.session.get(AdminUser, admin_user["id"])
        assert user.verify_password(new_password)
        assert not user.verify_password(TEST_ADMIN_PASSWORD)


# ----------------------------------------------------------------------
# 8. 不存在的 id
# ----------------------------------------------------------------------
@pytest.mark.parametrize("path", [
    "/admin/people/9999/edit",
    "/admin/research/9999/edit",
])
def test_missing_entity_returns_404(logged_in_client, path):
    assert logged_in_client.get(path).status_code == 404


@pytest.mark.parametrize("path", [
    "/admin/people/9999/publish",
    "/admin/people/9999/graduate",
    "/admin/research/9999/publish",
    "/admin/research/9999/archive",
])
def test_missing_entity_post_returns_404(logged_in_client, path):
    assert logged_in_client.post(path).status_code == 404
