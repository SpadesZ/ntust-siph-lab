# ============================================================
# NTUST SiPh Lab - Legacy Migration Acceptance Tests (AC-21 ~ AC-26)
#
# 上下游：
#   scripts/legacy_baseline.py（母站盤點）-> 本檔
#   scripts/seed_from_google_sites.py（匯入）-> 本檔驗證結果
#   scripts/verify_migration.py（驗證器）-> 本檔驗證驗證器本身
#   legacy/google_sites/*（證據檔案）-> 本檔檢查存在性
#
# 檔案路徑：tests/test_legacy_migration.py
# 建立日期：2026-08-14 / 版本：v1.0
#
# 模組定位：
#   ADR-011（Legacy Content Preservation Gate）的自動化驗收。
#   這組測試是「母站零遺漏」的最後防線 —— 若有人日後修改
#   legacy_baseline 或 seed 邏輯而遺漏內容，這裡會立刻失敗。
#
# 對應驗收條目：
#   AC-21 inventory 每筆都有 source/type/target/status，無空白
#   AC-22 Legacy Baseline 100% 可上線狀態；UNRESOLVED = 0
#   AC-23 教授資料、六項專長、Email、外鏈、四位成員可被找到
#   AC-24 媒體資產有 manifest 與 checksum
#   AC-25 平台 chrome 被排除，Calendar embed 有明確決策
#   AC-26 difference_report 無未解決缺漏
#
# 特殊機制：
#   多數測試在「已執行 seed 的乾淨資料庫」上進行。
#   使用 seeded_app fixture 而非直接讀開發資料庫 ——
#   測試必須可重現，不能依賴開發者本機的資料狀態。
#
# 驗證方式：
#   pytest tests/test_legacy_migration.py -v
# ============================================================

from __future__ import annotations

from pathlib import Path

import pytest

from scripts import legacy_baseline as baseline

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LEGACY_DIR = PROJECT_ROOT / "legacy" / "google_sites"


@pytest.fixture()
def seeded_app(app):
    """已執行母站 seed 的 app。"""
    from scripts.seed_from_google_sites import run_seed

    with app.app_context():
        run_seed(force=False)
    return app


# ----------------------------------------------------------------------
# AC-21：inventory 欄位完整性
# ----------------------------------------------------------------------
@pytest.mark.acceptance
def test_ac21_all_legacy_items_have_required_fields():
    """AC-21：所有 LC 項目都有 source、type、target、status，
    不得有空白 target/status。
    """
    required = (
        "legacy_id",
        "source_url",
        "captured_at",
        "content_type",
        "source_value",
        "target_entity",
        "target_url_or_field",
        "status",
        "verification",
    )

    assert baseline.LEGACY_ITEMS, "Legacy inventory 不得為空"

    for item in baseline.LEGACY_ITEMS:
        for field in required:
            value = str(getattr(item, field, "") or "").strip()
            assert value, f"{item.legacy_id} 的 {field} 欄位為空（AC-21）"


@pytest.mark.acceptance
def test_ac21_inventory_covers_lc001_to_lc019():
    """SAI §2.1 定義 LC-001 ~ LC-019，一筆都不能少。"""
    expected = {f"LC-{n:03d}" for n in range(1, 20)}
    actual = {item.legacy_id for item in baseline.LEGACY_ITEMS}

    missing = expected - actual
    assert not missing, f"Legacy inventory 缺少項目：{sorted(missing)}"


@pytest.mark.acceptance
def test_ac21_legacy_ids_are_unique():
    """legacy_id 不得重複（重複會讓 mapping 對不上）。"""
    ids = [item.legacy_id for item in baseline.LEGACY_ITEMS]
    assert len(ids) == len(set(ids)), "legacy_id 有重複"


@pytest.mark.acceptance
def test_ac21_approval_required_for_rewrite_or_remove():
    """SAI §22.2：APPROVED_REWRITE / APPROVED_REMOVE 必須有核准紀錄。"""
    for item in baseline.LEGACY_ITEMS:
        if item.requires_approval():
            assert item.approval.strip(), (
                f"{item.legacy_id} 狀態為 {item.status}，必須填寫核准人、日期與理由"
            )
            # 核准紀錄必須包含可稽核的關鍵資訊。
            assert "核准" in item.approval or "approval" in item.approval.lower()
            assert "2026" in item.approval, "核准紀錄必須包含日期"


def test_status_constants_match_application_model():
    """legacy_baseline 的狀態字面值必須與 app.models 一致。

    legacy_baseline 刻意重複宣告這些常數以便離線執行，
    但兩處若漂移，verify_migration 會誤判。
    """
    from app.models.mixins import MigrationStatus

    assert baseline.STATUS_MIGRATED == MigrationStatus.MIGRATED
    assert baseline.STATUS_APPROVED_REWRITE == MigrationStatus.APPROVED_REWRITE
    assert baseline.STATUS_APPROVED_REMOVE == MigrationStatus.APPROVED_REMOVE
    assert baseline.STATUS_REVIEW_REQUIRED == MigrationStatus.REVIEW_REQUIRED
    assert baseline.STATUS_UNRESOLVED == MigrationStatus.UNRESOLVED


# ----------------------------------------------------------------------
# AC-22：UNRESOLVED = 0
# ----------------------------------------------------------------------
@pytest.mark.acceptance
def test_ac22_no_blocking_status_remains():
    """AC-22：所有項目必須是 MIGRATED / APPROVED_REWRITE /
    APPROVED_REMOVE；UNRESOLVED = 0。
    """
    blocking = baseline.blocking_items()

    assert not blocking, (
        "以下項目阻擋上線（AC-22 要求 UNRESOLVED = 0）：\n"
        + "\n".join(f"  {i.legacy_id}: {i.status} — {i.source_value}" for i in blocking)
    )


@pytest.mark.acceptance
def test_ac22_status_summary_is_complete():
    """統計數字必須涵蓋全部項目。"""
    summary = baseline.status_summary()
    assert sum(summary.values()) == len(baseline.LEGACY_ITEMS)


# ----------------------------------------------------------------------
# AC-23：母站內容可在新站被找到
# ----------------------------------------------------------------------
@pytest.mark.acceptance
def test_ac23_professor_fields_are_migrated_verbatim(seeded_app):
    """AC-23：教授姓名、職稱、學歷、Email、外鏈逐字相符。"""
    from sqlalchemy import select

    from app.extensions import db
    from app.models.person import Person

    with seeded_app.app_context():
        prof = db.session.scalar(
            select(Person).where(Person.legacy_id == baseline.PROFESSOR["legacy_id"])
        )
        assert prof is not None, "找不到教授資料"

        assert prof.name_zh == baseline.PROFESSOR["name_zh"]
        assert prof.name_en == baseline.PROFESSOR["name_en"]
        assert prof.title_zh == baseline.PROFESSOR["title_zh"]
        assert prof.education_zh == baseline.PROFESSOR["education_zh"]
        assert prof.email_public == baseline.PROFESSOR["email_public"]
        assert prof.external_url == baseline.PROFESSOR["external_url"]


@pytest.mark.acceptance
def test_ac23_professor_is_visible_on_public_site(seeded_app):
    """AC-23：教授資料實際出現在前台頁面上。"""
    client = seeded_app.test_client()

    for path in ("/about", "/members"):
        html = client.get(path).get_data(as_text=True)
        assert baseline.PROFESSOR["name_zh"] in html, f"{path} 缺少教授姓名"
        assert baseline.PROFESSOR["title_zh"] in html, f"{path} 缺少教授職稱"
        assert baseline.PROFESSOR["email_public"] in html, f"{path} 缺少教授 Email"


@pytest.mark.acceptance
def test_ac23_all_six_expertise_items_present(seeded_app):
    """AC-23 / SAI §22.3：六項專長集合比對，不可漏項。"""
    client = seeded_app.test_client()

    for path in ("/", "/about"):
        html = client.get(path).get_data(as_text=True)
        missing = [e["title_zh"] for e in baseline.EXPERTISE if e["title_zh"] not in html]
        assert not missing, f"{path} 缺少專長項目：{missing}"

    # 教授頁也必須完整呈現六項。
    from sqlalchemy import select

    from app.extensions import db
    from app.models.person import Person

    with seeded_app.app_context():
        prof = db.session.scalar(
            select(Person).where(Person.legacy_id == baseline.PROFESSOR["legacy_id"])
        )
        for item in baseline.EXPERTISE:
            assert item["title_zh"] in (prof.research_focus_zh or ""), (
                f"教授研究焦點缺少「{item['title_zh']}」"
            )


@pytest.mark.acceptance
def test_ac23_expertise_count_is_exactly_six():
    """母站專長為六項，不多不少（防止日後誤增誤刪）。"""
    assert len(baseline.EXPERTISE) == 6


@pytest.mark.acceptance
def test_ac23_all_four_students_are_published_and_findable(seeded_app):
    """AC-23：四位碩二生姓名可在 /members 被找到。"""
    from sqlalchemy import select

    from app.extensions import db
    from app.models.mixins import PublishStatus
    from app.models.person import Person

    client = seeded_app.test_client()
    members_html = client.get("/members").get_data(as_text=True)

    with seeded_app.app_context():
        for student in baseline.STUDENTS:
            person = db.session.scalar(
                select(Person).where(Person.legacy_id == student["legacy_id"])
            )
            assert person is not None, f"{student['legacy_id']} 找不到成員資料"
            assert person.name_zh == student["name_zh"], "姓名必須逐字相符"
            assert person.publish_status == PublishStatus.PUBLISHED, (
                f"{student['name_zh']} 必須發布才能滿足 AC-23"
            )

            # 個人頁必須可存取。
            assert client.get(f"/people/{person.slug}").status_code == 200

            # 姓名必須出現在列表頁。
            assert student["name_zh"] in members_html


@pytest.mark.acceptance
def test_ac23_students_marked_as_pending_detail(seeded_app):
    """四位學生必須標記為待補（ADR-012 的核准條件）。

    ADR-012 全文：docs/adr/ADR-012-legacy-pending-publish-exemption.md
    （SAI 的 ADR 表僅到 ADR-011；本專案新增決策一律放 docs/adr/）。

    這個標記是「發布但缺研究焦點」得以被允許的唯一依據，
    若被移除，這四筆資料就變成違反 §15.1 的一般內容。
    """
    from sqlalchemy import select

    from app.extensions import db
    from app.models.person import Person

    with seeded_app.app_context():
        for student in baseline.STUDENTS:
            person = db.session.scalar(
                select(Person).where(Person.legacy_id == student["legacy_id"])
            )
            assert person.legacy_pending_detail is True, (
                f"{student['name_zh']} 必須標記 legacy_pending_detail"
            )


@pytest.mark.acceptance
def test_ac23_no_fabricated_data_for_students(seeded_app):
    """SAI §2.3：母站沒有的資料不得被填入。

    這是最重要的「反向」測試：確認系統「沒有」捏造內容。
    """
    from sqlalchemy import select

    from app.extensions import db
    from app.models.person import Person

    with seeded_app.app_context():
        for student in baseline.STUDENTS:
            person = db.session.scalar(
                select(Person).where(Person.legacy_id == student["legacy_id"])
            )
            assert not person.name_en, "母站沒有學生英文姓名，不得填入（SAI §2.3）"
            assert not person.research_focus_zh, "母站沒有學生研究方向，不得填入"
            assert not person.thesis_title_zh, "母站沒有學生論文題目，不得填入"
            assert not person.email_public, "母站沒有學生 Email，不得填入"


@pytest.mark.acceptance
def test_ac23_no_fabricated_research_outputs(seeded_app):
    """母站沒有研究成果，seed 後資料庫必須是空的。"""
    from sqlalchemy import func, select

    from app.extensions import db
    from app.models.research_output import ResearchOutput

    with seeded_app.app_context():
        count = db.session.scalar(select(func.count(ResearchOutput.id)))
        assert count == 0, (
            "母站沒有研究成果，系統不得自行產生（SAI §2.3、§14.3）"
        )


@pytest.mark.acceptance
def test_ac23_no_fabricated_alumni(seeded_app):
    """母站沒有畢業生，seed 後不得存在。"""
    from sqlalchemy import func, select

    from app.extensions import db
    from app.models.mixins import PersonStatus
    from app.models.person import Person

    with seeded_app.app_context():
        count = db.session.scalar(
            select(func.count(Person.id)).where(Person.status == PersonStatus.ALUMNI)
        )
        assert count == 0, "母站沒有畢業生，系統不得自行產生"


@pytest.mark.acceptance
def test_ac23_lab_name_migrated(seeded_app):
    """LC-001：Lab 名稱逐字相符並出現在頁面上。"""
    from app.models.site_setting import SiteSetting

    with seeded_app.app_context():
        setting = SiteSetting.get()
        assert setting.lab_name_zh == baseline.LAB_NAME

    html = seeded_app.test_client().get("/").get_data(as_text=True)
    assert baseline.LAB_NAME in html


@pytest.mark.acceptance
def test_ac23_ntust_external_link_present(seeded_app):
    """LC-013：NTUST 外鏈出現在前台。"""
    client = seeded_app.test_client()
    html = client.get("/about").get_data(as_text=True)
    assert baseline.PROFESSOR["external_url"] in html


# ----------------------------------------------------------------------
# AC-24：媒體 manifest 與 checksum
# ----------------------------------------------------------------------
@pytest.mark.acceptance
def test_ac24_professor_photo_asset_exists_with_correct_checksum():
    """AC-24：LC-002 原始資產存在且 checksum 相符。"""
    import hashlib

    asset = PROJECT_ROOT / baseline.PROFESSOR["photo_asset"]
    assert asset.is_file(), (
        f"LC-002 原始資產不存在：{asset}。"
        "依 SAI §23.1，缺資產必須列為 UNRESOLVED，不得以替代圖略過。"
    )

    actual = hashlib.sha256(asset.read_bytes()).hexdigest()
    assert actual == baseline.PROFESSOR["photo_sha256"], (
        f"LC-002 資產 checksum 不符：\n  預期 {baseline.PROFESSOR['photo_sha256']}\n"
        f"  實際 {actual}"
    )


@pytest.mark.acceptance
def test_ac24_photo_is_stored_and_referenced(seeded_app):
    """AC-24：照片已寫入 storage 且 DB 有正確引用。"""
    from sqlalchemy import select

    from app.extensions import db
    from app.models.person import Person
    from app.storage import get_storage

    with seeded_app.app_context():
        prof = db.session.scalar(
            select(Person).where(Person.legacy_id == baseline.PROFESSOR["legacy_id"])
        )
        assert prof.photo_path, "教授照片未寫入 people.photo_path"
        assert get_storage().exists(prof.photo_path), (
            f"photo_path 指向 {prof.photo_path}，但物件不存在於 storage"
        )
        # 有圖必有 alt（SAI §16、AC-12）。
        assert prof.photo_alt_zh


@pytest.mark.acceptance
def test_ac24_media_manifest_file_exists():
    """AC-24：media_manifest.csv 存在且包含 LC-002。"""
    manifest = LEGACY_DIR / "media_manifest.csv"
    assert manifest.is_file(), "缺少 media_manifest.csv（SAI §9.4、AC-24）"

    content = manifest.read_text(encoding="utf-8-sig")
    assert "LC-002" in content
    assert baseline.PROFESSOR["photo_sha256"] in content, "manifest 必須記錄 checksum"


@pytest.mark.acceptance
def test_ac24_seed_refuses_tampered_asset(app, tmp_path, monkeypatch):
    """資產 checksum 不符時必須中止，不得靜默使用錯誤檔案。"""
    from scripts import seed_from_google_sites as seeder

    # 建立一個內容不同的假資產。
    fake = tmp_path / "fake.jpg"
    fake.write_bytes(b"this is not the original photo")

    monkeypatch.setitem(baseline.PROFESSOR, "photo_asset", str(fake))
    monkeypatch.setattr(seeder, "PROJECT_ROOT", Path("/"))

    with app.app_context():
        with pytest.raises(RuntimeError, match="checksum"):
            seeder.run_seed(force=True)


# ----------------------------------------------------------------------
# AC-25：平台元素排除與 embed 決策
# ----------------------------------------------------------------------
@pytest.mark.acceptance
def test_ac25_platform_chrome_exclusions_recorded():
    """AC-25：SAI §2.2 的六項平台元素都有明確排除紀錄。"""
    expected = {
        "Search this site",
        "Skip to main content",
        "Skip to navigation",
        "Page updated",
        "Google Sites",
        "Report abuse",
    }
    recorded = {item.element for item in baseline.PLATFORM_EXCLUSIONS}

    missing = expected - recorded
    assert not missing, f"缺少平台元素排除紀錄：{sorted(missing)}"

    for item in baseline.PLATFORM_EXCLUSIONS:
        assert item.reason.strip(), f"「{item.element}」缺少排除理由"


@pytest.mark.acceptance
def test_ac25_calendar_embed_has_explicit_decision():
    """AC-25：LC-019 必須有明確的 MIGRATED 或 APPROVED_REMOVE 決策。"""
    calendar = baseline.find("LC-019")

    assert calendar is not None, "找不到 LC-019（Google Calendar embed）"
    assert calendar.status in (
        baseline.STATUS_MIGRATED,
        baseline.STATUS_APPROVED_REMOVE,
    ), (
        f"LC-019 的狀態必須是 MIGRATED 或 APPROVED_REMOVE，"
        f"實際為 {calendar.status}（AC-25）"
    )
    assert calendar.status != baseline.STATUS_REVIEW_REQUIRED

    if calendar.status == baseline.STATUS_APPROVED_REMOVE:
        assert calendar.approval.strip(), "APPROVED_REMOVE 必須有核准紀錄"


@pytest.mark.acceptance
def test_ac25_platform_chrome_not_present_on_new_site(seeded_app):
    """平台 boilerplate 不得出現在新站上。"""
    client = seeded_app.test_client()
    html = client.get("/").get_data(as_text=True)

    for element in ("Search this site", "Report abuse", "Page updated"):
        assert element not in html, f"新站不應包含 Google Sites 平台元素：{element}"


# ----------------------------------------------------------------------
# AC-26：difference report 無未解決缺漏
# ----------------------------------------------------------------------
@pytest.mark.acceptance
def test_ac26_evidence_files_exist():
    """SAI §9.4 要求的六份 legacy 證據檔案都必須存在。"""
    required = [
        "source_snapshot.md",
        "migration_inventory.csv",
        "content_mapping.csv",
        "media_manifest.csv",
        "difference_report.md",
        "content_signoff.md",
    ]
    for name in required:
        path = LEGACY_DIR / name
        assert path.is_file(), f"缺少遷移證據檔案：legacy/google_sites/{name}"
        assert path.stat().st_size > 0, f"{name} 為空檔"


@pytest.mark.acceptance
def test_ac26_verification_script_passes(seeded_app, tmp_path, monkeypatch):
    """AC-26：verify_migration 的所有檢查必須通過。

    直接呼叫驗證器的檢查函式，而非執行子程序 ——
    這樣失敗時能看到具體是哪一項不通過。
    """
    from scripts import verify_migration as verifier

    results = [
        verifier.check_inventory_completeness(),
        verifier.check_no_blocking_status(),
        verifier.check_content_present(seeded_app),
        verifier.check_media_manifest(seeded_app),
        verifier.check_platform_exclusions(),
        verifier.check_database_integrity(seeded_app),
    ]

    failures = [(r.ac, msg) for r in results for msg in r.failed]
    assert not failures, "遷移驗證失敗：\n" + "\n".join(
        f"  [{ac}] {msg}" for ac, msg in failures
    )


@pytest.mark.acceptance
def test_ac26_difference_report_declares_zero_unresolved():
    """AC-26：difference_report.md 必須宣告 UNRESOLVED = 0。"""
    report = (LEGACY_DIR / "difference_report.md").read_text(encoding="utf-8")

    assert "UNRESOLVED" in report
    assert "可進行 cutover" in report or "UNRESOLVED = 0" in report, (
        "difference_report 必須明確宣告 UNRESOLVED 為 0"
    )
    assert "阻擋上線" not in report.split("## 2.")[0], (
        "difference_report 摘要顯示仍有阻擋項目"
    )


@pytest.mark.acceptance
def test_ac26_signoff_records_the_three_decisions():
    """content_signoff.md 必須記錄三項管理者裁示。"""
    signoff = (LEGACY_DIR / "content_signoff.md").read_text(encoding="utf-8")

    assert "LC-019" in signoff, "缺少 Google Calendar 的裁示紀錄"
    assert "APPROVED_REMOVE" in signoff
    assert "legacy_pending_detail" in signoff, "缺少四位成員發布策略的裁示紀錄"
    assert "2026-08-15" in signoff, "缺少核准日期"


# ----------------------------------------------------------------------
# seed 冪等性
# ----------------------------------------------------------------------
def test_seed_is_idempotent(app):
    """重複執行 seed 不會產生重複資料。"""
    from sqlalchemy import func, select

    from app.extensions import db
    from app.models.person import Person
    from scripts.seed_from_google_sites import run_seed

    with app.app_context():
        run_seed(force=False)
        first_count = db.session.scalar(select(func.count(Person.id)))

        run_seed(force=False)
        second_count = db.session.scalar(select(func.count(Person.id)))

        assert first_count == second_count, "重複 seed 不得產生重複人物"
        assert first_count == 5, "應為 1 位教授 + 4 位碩二生"


def test_seed_does_not_overwrite_admin_edits(app):
    """已由管理者補齊的欄位，重跑 seed 不得覆寫。

    這是 seed 腳本冪等性設計的核心：研究室補完學生研究方向後
    重跑 seed，資料不能被清掉。
    """
    from sqlalchemy import select

    from app.extensions import db
    from app.models.person import Person
    from app.services.person_service import PersonService
    from scripts.seed_from_google_sites import run_seed

    with app.app_context():
        run_seed(force=False)

        person = db.session.scalar(
            select(Person).where(Person.legacy_id == "LC-015")
        )
        PersonService.update(
            person,
            {
                "name_zh": person.name_zh,
                "status": person.status,
                "title_zh": person.title_zh,
                "research_focus_zh": "管理者補上的研究方向",
                "name_en": "Admin Filled Name",
            },
        )

        run_seed(force=False)
        db.session.refresh(person)

        assert person.research_focus_zh == "管理者補上的研究方向"
        assert person.name_en == "Admin Filled Name"
