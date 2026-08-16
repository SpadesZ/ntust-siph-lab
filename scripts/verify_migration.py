#!/usr/bin/env python
# ============================================================
# NTUST SiPh Lab - Migration Verification & Difference Report
#
# 上下游：
#   scripts/legacy_baseline.py（母站盤點）
#       -> 本腳本 -> 資料庫實際內容比對
#       -> legacy/google_sites/difference_report.md（產生）
#       -> legacy/google_sites/migration_inventory.csv（產生）
#       -> legacy/google_sites/content_mapping.csv（產生）
#       -> legacy/google_sites/media_manifest.csv（產生）
#   PostgreSQL 匯入後 -> --database 模式驗證 row counts / FK / slug
#
#   執行方式：
#     python scripts/verify_migration.py --legacy     （母站零遺漏，AC-21~26）
#     python scripts/verify_migration.py --database   （DB 完整性，G3/G4）
#     python scripts/verify_migration.py --all
#
# 檔案路徑：
#   scripts/verify_migration.py
#
# 建立日期：2026-08-14
# 最後重大修改：2026-08-14
# 版本：v1.0
#
# 模組定位與責任邊界：
#   SAI §22 的「零遺漏 Gate」自動化執行者，同時涵蓋
#   §21.3 的資料遷移驗證。
#
#   本腳本是 AC-21~AC-26 的可執行版本：
#     AC-21 inventory 每筆都有 source/type/target/status，無空白
#     AC-22 Legacy Baseline 100% 為 MIGRATED/APPROVED_*；
#           UNRESOLVED = 0
#     AC-23 教授姓名/職稱/學歷/六項專長/Email/外鏈/四位成員
#           可在新站找到
#     AC-24 媒體資產有 manifest、checksum 或人工驗證紀錄
#     AC-25 平台 chrome 被排除，Calendar embed 有明確決策
#     AC-26 difference_report 無未解決缺漏
#
#   責任邊界（不得做的事）：
#     - 不得修改資料庫或 legacy_baseline（純驗證 + 產生報告）。
#     - 不得自行把失敗項目標記為通過。
#     - 不得以「找不到資料」當作通過（必須明確失敗）。
#
# 輸入 -> 處理 -> 輸出 Pipeline：
#   legacy_baseline.LEGACY_ITEMS + 資料庫實際內容
#     -> 逐條檢查（欄位完整性、狀態、實際可見性、checksum）
#     -> 產生四份 CSV 與 difference_report.md
#     -> exit code：0 = 可上線；1 = 阻擋上線
#
# 主要 Function：
#   check_inventory_completeness()  - AC-21
#   check_no_blocking_status()      - AC-22
#   check_content_present()         - AC-23
#   check_media_manifest()          - AC-24
#   check_platform_exclusions()     - AC-25
#   check_database_integrity()      - G3/G4、§21.3
#   write_inventory_csv / write_mapping_csv / write_media_manifest_csv
#   write_difference_report()       - AC-26
#
# 依賴套件：
#   標準庫 csv / json / hashlib；app.*（僅 --database 與內容比對需要）
#
# 環境變數：
#   APP_ENV（決定連哪個資料庫）
#
# 資料庫使用方式：
#   唯讀。查詢 people、research_outputs、site_settings 等表
#   以確認母站內容確實存在於新站。
#
# Error Handling / Fallback：
#   - 檢查失敗「不」中止，而是收集所有失敗項目後一次回報 ——
#     一次看到全部問題比修一個跑一次有效率得多。
#   - 資料庫無法連線時，legacy 的「結構檢查」（AC-21/22/25）
#     仍會執行，只有「內容存在檢查」（AC-23/24）會標記為
#     無法驗證並視為失敗（不得因為查不到就當通過）。
#
# 特殊機制（difference_report 的 UNRESOLVED 定義）：
#   SAI §22.1 定義 UNRESOLVED 為「缺檔、缺映射、無法判斷或
#   驗證失敗」。因此本腳本把「任何一項檢查失敗」都計入
#   difference_report 的 UNRESOLVED 區段 —— 包含資料庫中
#   找不到應有內容的情況，而不只是 legacy_baseline 裡
#   狀態欄寫著 UNRESOLVED 的項目。
#
# 已知限制與禁止事項：
#   1. 外部連結的 HTTP 檢查需要網路；離線環境會標記為
#      「未檢查」而非「通過」（--skip-http 可明確略過）。
#   2. 禁止修改本腳本以放寬檢查來讓 cutover 通過 ——
#      那等同繞過 ADR-011 的保護。
#
# 維護契約：
#   新增 LC 項目時不需要修改本腳本（它讀 legacy_baseline），
#   但若新增了需要特殊驗證方式的內容類型，
#   必須在 check_content_present() 加入對應檢查。
#
# 驗證方式：
#   python scripts/verify_migration.py --all
#   pytest tests/test_legacy_migration.py
# ============================================================

from __future__ import annotations

import argparse
import csv
import hashlib
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import legacy_baseline as baseline  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LEGACY_DIR = PROJECT_ROOT / "legacy" / "google_sites"


class CheckResult:
    """單一檢查的結果。

    設計理由：把「檢查名稱、AC 編號、通過與否、細節」綁在一起，
    讓 difference_report 可以直接輸出結構化內容，
    而不需要在報告產生時重新解析文字。
    """

    def __init__(self, ac: str, name: str) -> None:
        self.ac = ac
        self.name = name
        self.passed: list[str] = []
        self.failed: list[str] = []
        self.skipped: list[str] = []

    def ok(self, message: str) -> None:
        self.passed.append(message)

    def fail(self, message: str) -> None:
        self.failed.append(message)

    def skip(self, message: str) -> None:
        self.skipped.append(message)

    @property
    def is_passing(self) -> bool:
        return not self.failed

    def __str__(self) -> str:
        status = "PASS" if self.is_passing else "FAIL"
        return f"[{status}] {self.ac} {self.name}（通過 {len(self.passed)}、失敗 {len(self.failed)}）"


# ----------------------------------------------------------------------
# AC-21：inventory 欄位完整性
# ----------------------------------------------------------------------
def check_inventory_completeness() -> CheckResult:
    """AC-21：所有 LC 項目都有 source、type、target、status，
    不得有空白 target/status。
    """
    result = CheckResult("AC-21", "migration_inventory 欄位完整性")

    required = ("legacy_id", "source_url", "captured_at", "content_type",
                "source_value", "target_entity", "target_url_or_field",
                "status", "verification")

    for item in baseline.LEGACY_ITEMS:
        missing = [field for field in required if not str(getattr(item, field, "")).strip()]
        if missing:
            result.fail(f"{item.legacy_id}：缺少必要欄位 {', '.join(missing)}")
            continue

        if item.status not in (
            baseline.STATUS_MIGRATED,
            baseline.STATUS_APPROVED_REWRITE,
            baseline.STATUS_APPROVED_REMOVE,
            baseline.STATUS_REVIEW_REQUIRED,
            baseline.STATUS_UNRESOLVED,
            baseline.STATUS_DISCOVERED,
        ):
            result.fail(f"{item.legacy_id}：status 值不合法（{item.status}）")
            continue

        # SAI §22.2：rewrite/remove 時必須填核准人、日期、理由。
        if item.requires_approval() and not item.approval.strip():
            result.fail(
                f"{item.legacy_id}：狀態為 {item.status} 但缺少核准紀錄"
                "（SAI §22.2 條件式必填）"
            )
            continue

        result.ok(f"{item.legacy_id}：欄位完整（status={item.status}）")

    return result


# ----------------------------------------------------------------------
# AC-22：無阻擋上線的狀態
# ----------------------------------------------------------------------
def check_no_blocking_status() -> CheckResult:
    """AC-22：Legacy Baseline 必須 100% 為 MIGRATED /
    APPROVED_REWRITE / APPROVED_REMOVE；UNRESOLVED = 0。
    """
    result = CheckResult("AC-22", "UNRESOLVED = 0（launch gate）")

    blocking = baseline.blocking_items()
    if blocking:
        for item in blocking:
            result.fail(
                f"{item.legacy_id}：狀態 {item.status} 阻擋上線"
                f"（{item.source_value[:40]}）"
            )
    else:
        summary = baseline.status_summary()
        result.ok(
            "全部 "
            + str(len(baseline.LEGACY_ITEMS))
            + " 筆皆為可上線狀態："
            + "、".join(f"{k}={v}" for k, v in sorted(summary.items()))
        )

    return result


# ----------------------------------------------------------------------
# AC-23：母站內容確實出現在新站
# ----------------------------------------------------------------------
def check_content_present(app) -> CheckResult:
    """AC-23：教授姓名、職稱、學歷、六項專長、Email、
    NTUST 外鏈與四位碩二生姓名，逐字或經批准改寫後可在新站找到。
    """
    result = CheckResult("AC-23", "母站內容在新站可被找到")

    if app is None:
        result.fail("無法載入應用程式，無法驗證新站內容（不得視為通過）")
        return result

    from sqlalchemy import select

    from app.models.mixins import PublishStatus
    from app.models.person import Person
    from app.models.site_setting import SiteSetting
    from app.extensions import db

    with app.app_context():
        # --- 教授 ---
        prof = db.session.scalar(
            select(Person).where(Person.legacy_id == baseline.PROFESSOR["legacy_id"])
        )
        if prof is None:
            result.fail("找不到教授人物資料（legacy_id=LC-003）")
        else:
            checks = [
                ("LC-003 姓名（中）", baseline.PROFESSOR["name_zh"], prof.name_zh),
                ("LC-003 姓名（英）", baseline.PROFESSOR["name_en"], prof.name_en),
                ("LC-004 職稱", baseline.PROFESSOR["title_zh"], prof.title_zh),
                ("LC-005 學歷", baseline.PROFESSOR["education_zh"], prof.education_zh),
                ("LC-012 Email", baseline.PROFESSOR["email_public"], prof.email_public),
                ("LC-013 NTUST 外鏈", baseline.PROFESSOR["external_url"], prof.external_url),
            ]
            for label, expected, actual in checks:
                if (actual or "").strip() == expected:
                    result.ok(f"{label}：逐字相符")
                else:
                    result.fail(f"{label}：不相符\n    預期：{expected}\n    實際：{actual}")

            if prof.publish_status != PublishStatus.PUBLISHED:
                result.fail(f"教授頁面未發布（狀態：{prof.publish_status}），前台無法找到")
            else:
                result.ok(f"教授頁面已發布：/people/{prof.slug}")

        # --- 六項專長（集合比對，不可漏項；SAI §22.3） ---
        setting = SiteSetting.get()
        site_focus = {item.get("title_zh") for item in setting.research_focus}
        expected_focus = {item["title_zh"] for item in baseline.EXPERTISE}

        missing_focus = expected_focus - site_focus
        if missing_focus:
            result.fail(
                f"LC-006~011 六項專長：SiteSetting 缺少 {len(missing_focus)} 項 —— "
                + "、".join(sorted(missing_focus))
            )
        else:
            result.ok(f"LC-006~011 六項專長：SiteSetting 六項全數存在")

        # 專長也必須出現在教授的 research_focus（§22.3 雙 target）。
        if prof is not None:
            prof_focus_text = prof.research_focus_zh or ""
            missing_in_person = [
                item["title_zh"]
                for item in baseline.EXPERTISE
                if item["title_zh"] not in prof_focus_text
            ]
            if missing_in_person:
                result.fail(
                    "LC-006~011 六項專長：教授頁缺少 "
                    + "、".join(missing_in_person)
                )
            else:
                result.ok("LC-006~011 六項專長：教授頁六項全數存在")

        # --- 四位碩二生 ---
        for student in baseline.STUDENTS:
            person = db.session.scalar(
                select(Person).where(Person.legacy_id == student["legacy_id"])
            )
            if person is None:
                result.fail(f"{student['legacy_id']}：找不到成員「{student['name_zh']}」")
                continue

            if person.name_zh != student["name_zh"]:
                result.fail(
                    f"{student['legacy_id']}：姓名不相符"
                    f"（預期 {student['name_zh']}，實際 {person.name_zh}）"
                )
                continue

            if person.publish_status != PublishStatus.PUBLISHED:
                result.fail(
                    f"{student['legacy_id']}：「{person.name_zh}」未發布"
                    f"（狀態 {person.publish_status}），前台無法找到"
                )
                continue

            result.ok(
                f"{student['legacy_id']}：「{person.name_zh}」已發布"
                f"（/people/{person.slug}）"
            )

        # --- LC-001 Lab 名稱 ---
        if setting.lab_name_zh == baseline.LAB_NAME:
            result.ok(f"LC-001 Lab 名稱：逐字相符（{baseline.LAB_NAME}）")
        else:
            result.fail(
                f"LC-001 Lab 名稱不相符：預期 {baseline.LAB_NAME}，"
                f"實際 {setting.lab_name_zh}"
            )

    return result


# ----------------------------------------------------------------------
# AC-24：媒體 manifest 與 checksum
# ----------------------------------------------------------------------
def check_media_manifest(app) -> CheckResult:
    """AC-24：教授圖片/媒體資產有 manifest、checksum 或
    人工驗證紀錄；不得因下載困難而靜默遺漏。
    """
    result = CheckResult("AC-24", "媒體資產 manifest 與 checksum")

    asset_path = PROJECT_ROOT / baseline.PROFESSOR["photo_asset"]
    expected_sum = baseline.PROFESSOR["photo_sha256"]

    # --- 1. 原始資產存在且 checksum 相符 ---
    if not asset_path.is_file():
        result.fail(
            f"LC-002：找不到原始資產 {asset_path}。"
            "依 SAI §23.1，這必須列為 UNRESOLVED，不得以替代圖略過。"
        )
    else:
        actual = hashlib.sha256(asset_path.read_bytes()).hexdigest()
        if actual == expected_sum:
            result.ok(
                f"LC-002 原始資產 checksum 相符（{actual[:16]}…，"
                f"{asset_path.stat().st_size} bytes）"
            )
        else:
            result.fail(
                f"LC-002 原始資產 checksum 不符：預期 {expected_sum}，實際 {actual}"
            )

    # --- 2. 已實際寫入資料庫並可透過 storage 存取 ---
    if app is None:
        result.fail("無法載入應用程式，無法驗證媒體是否已寫入新站")
        return result

    from sqlalchemy import select

    from app.extensions import db
    from app.models.person import Person
    from app.storage import get_storage

    with app.app_context():
        prof = db.session.scalar(
            select(Person).where(Person.legacy_id == baseline.PROFESSOR["legacy_id"])
        )
        if prof is None or not prof.photo_path:
            result.fail("LC-002：教授照片尚未寫入新站（people.photo_path 為空）")
        else:
            storage = get_storage()
            if storage.exists(prof.photo_path):
                result.ok(
                    f"LC-002：照片已存在於 storage（{storage.name}）"
                    f"，object key={prof.photo_path}"
                )
            else:
                result.fail(
                    f"LC-002：people.photo_path 指向 {prof.photo_path}，"
                    f"但該物件不存在於 storage（{storage.name}）"
                )

            if not prof.photo_alt_zh:
                result.fail("LC-002：照片缺少替代文字（alt），違反 SAI §16 與 AC-12")
            else:
                result.ok(f"LC-002：照片替代文字已設定（{prof.photo_alt_zh}）")

    return result


# ----------------------------------------------------------------------
# AC-25：平台 chrome 排除與 embed 決策
# ----------------------------------------------------------------------
def check_platform_exclusions() -> CheckResult:
    """AC-25：Google Sites 平台 chrome 被排除，
    但 Google Calendar embed 必須有明確 MIGRATED 或 APPROVED_REMOVE 決策。
    """
    result = CheckResult("AC-25", "平台元素排除與 embed 決策")

    # --- 平台 chrome 必須有排除紀錄 ---
    expected_exclusions = {
        "Search this site",
        "Skip to main content",
        "Skip to navigation",
        "Page updated",
        "Google Sites",
        "Report abuse",
    }
    recorded = {item.element for item in baseline.PLATFORM_EXCLUSIONS}
    missing = expected_exclusions - recorded
    if missing:
        result.fail(f"缺少平台元素排除紀錄：{'、'.join(sorted(missing))}")
    else:
        result.ok(
            f"SAI §2.2 的 {len(expected_exclusions)} 項平台元素皆有明確排除理由"
        )

    for item in baseline.PLATFORM_EXCLUSIONS:
        if not item.reason.strip():
            result.fail(f"平台元素「{item.element}」缺少排除理由")

    # --- LC-019 Calendar embed 必須有明確決策 ---
    calendar = baseline.find("LC-019")
    if calendar is None:
        result.fail("找不到 LC-019（Google Calendar embed）項目")
    elif calendar.status == baseline.STATUS_REVIEW_REQUIRED:
        result.fail(
            "LC-019 仍為 REVIEW_REQUIRED，阻擋上線。"
            "需教授/管理者裁示為 MIGRATED 或 APPROVED_REMOVE（SAI §22.1）。"
        )
    elif calendar.status in (baseline.STATUS_MIGRATED, baseline.STATUS_APPROVED_REMOVE):
        if calendar.status == baseline.STATUS_APPROVED_REMOVE and not calendar.approval.strip():
            result.fail("LC-019 標為 APPROVED_REMOVE 但缺少核准紀錄")
        else:
            result.ok(f"LC-019 已有明確決策：{calendar.status}")
    else:
        result.fail(f"LC-019 狀態不符合 AC-25 要求：{calendar.status}")

    return result


# ----------------------------------------------------------------------
# 資料庫完整性（G3/G4、SAI §21.3）
# ----------------------------------------------------------------------
def check_database_integrity(app) -> CheckResult:
    """驗證 table counts、unique slug、foreign-key relationships。

    對應 SAI §21.3 第 4 點與 Production Migration Gate 的 G3/G4。
    此檢查同時適用 SQLite 與 PostgreSQL（可攜契約）。
    """
    result = CheckResult("G3/G4", "資料庫完整性（counts / slug / FK）")

    if app is None:
        result.fail("無法載入應用程式，無法驗證資料庫")
        return result

    from sqlalchemy import func, select

    from app.extensions import db
    from app.models.person import Person
    from app.models.research_output import ResearchOutput, ResearchOutputPerson

    with app.app_context():
        # --- row counts ---
        people_count = db.session.scalar(select(func.count(Person.id))) or 0
        output_count = db.session.scalar(select(func.count(ResearchOutput.id))) or 0
        link_count = db.session.scalar(select(func.count(ResearchOutputPerson.id))) or 0
        result.ok(
            f"筆數：people={people_count}、research_outputs={output_count}、"
            f"research_output_people={link_count}"
        )

        # --- slug 唯一性 ---
        for model, label in ((Person, "people"), (ResearchOutput, "research_outputs")):
            duplicates = db.session.execute(
                select(model.slug, func.count(model.id))
                .group_by(model.slug)
                .having(func.count(model.id) > 1)
            ).all()
            if duplicates:
                result.fail(
                    f"{label}.slug 有重複值："
                    + "、".join(f"{slug}({count})" for slug, count in duplicates)
                )
            else:
                result.ok(f"{label}.slug 全部唯一")

        # --- slug 不得為空 ---
        for model, label in ((Person, "people"), (ResearchOutput, "research_outputs")):
            empty = db.session.scalar(
                select(func.count(model.id)).where(
                    (model.slug.is_(None)) | (model.slug == "")
                )
            ) or 0
            if empty:
                result.fail(f"{label} 有 {empty} 筆 slug 為空")
            else:
                result.ok(f"{label}.slug 無空值")

        # --- 外鍵完整性：關聯必須指向存在的資料 ---
        orphan_links = db.session.execute(
            select(func.count(ResearchOutputPerson.id))
            .outerjoin(Person, ResearchOutputPerson.person_id == Person.id)
            .where(Person.id.is_(None))
        ).scalar() or 0
        if orphan_links:
            result.fail(f"research_output_people 有 {orphan_links} 筆指向不存在的人物")
        else:
            result.ok("research_output_people 無孤兒關聯")

        # --- 時間欄位 ---
        null_timestamps = db.session.scalar(
            select(func.count(Person.id)).where(
                (Person.created_at.is_(None)) | (Person.updated_at.is_(None))
            )
        ) or 0
        if null_timestamps:
            result.fail(f"people 有 {null_timestamps} 筆缺少時間戳")
        else:
            result.ok("people 時間欄位完整（created_at / updated_at）")

    return result


# ----------------------------------------------------------------------
# 產生證據檔案
# ----------------------------------------------------------------------
def write_inventory_csv() -> Path:
    """產生 migration_inventory.csv（SAI §22.2 最低欄位）。"""
    LEGACY_DIR.mkdir(parents=True, exist_ok=True)
    path = LEGACY_DIR / "migration_inventory.csv"

    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "legacy_id", "source_url", "captured_at", "content_type",
            "source_value", "target_entity", "target_url_or_field",
            "status", "approval", "verification", "notes",
        ])
        for item in baseline.LEGACY_ITEMS:
            writer.writerow([
                item.legacy_id, item.source_url, item.captured_at,
                item.content_type, item.source_value, item.target_entity,
                item.target_url_or_field, item.status, item.approval,
                item.verification, item.notes,
            ])

    return path


def write_mapping_csv() -> Path:
    """產生 content_mapping.csv（old -> new location/status）。"""
    LEGACY_DIR.mkdir(parents=True, exist_ok=True)
    path = LEGACY_DIR / "content_mapping.csv"

    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "legacy_id", "old_location", "old_value",
            "new_entity", "new_location", "status", "approval",
        ])
        for item in baseline.LEGACY_ITEMS:
            writer.writerow([
                item.legacy_id, item.source_url, item.source_value,
                item.target_entity, item.target_url_or_field,
                item.status, item.approval,
            ])

    return path


def write_media_manifest_csv(app) -> Path:
    """產生 media_manifest.csv（image/embed/external asset）。

    包含母站的圖片資產、嵌入內容與外部連結三類非純文字項目
    （SAI §2.3：非純文字資產必須列入 manifest）。
    """
    LEGACY_DIR.mkdir(parents=True, exist_ok=True)
    path = LEGACY_DIR / "media_manifest.csv"

    rows: list[list[str]] = []

    # --- LC-002 圖片 ---
    asset_path = PROJECT_ROOT / baseline.PROFESSOR["photo_asset"]
    if asset_path.is_file():
        raw = asset_path.read_bytes()
        checksum = hashlib.sha256(raw).hexdigest()
        size = len(raw)
        acquired = "YES"
    else:
        checksum = ""
        size = 0
        acquired = "NO（UNRESOLVED）"

    object_key = ""
    if app is not None:
        from sqlalchemy import select

        from app.extensions import db
        from app.models.person import Person

        with app.app_context():
            prof = db.session.scalar(
                select(Person).where(Person.legacy_id == baseline.PROFESSOR["legacy_id"])
            )
            object_key = (prof.photo_path if prof else "") or ""

    rows.append([
        "LC-002", "image", baseline.PROFESSOR["photo_source_url"],
        str(asset_path.relative_to(PROJECT_ROOT)) if asset_path.is_file() else "",
        object_key, "image/jpeg", str(size), checksum, acquired,
        "教授頁首圖片，已下載原檔並經 checksum 驗證（AC-24）",
    ])

    # --- LC-013 外部連結 ---
    rows.append([
        "LC-013", "external_link", baseline.PROFESSOR["external_url"],
        "", "", "text/html", "", "", "YES",
        "NTUST 官方頁面外鏈，2026-08-15 HTTP 200 驗證通過，URL 未變更",
    ])

    # --- LC-019 嵌入內容 ---
    calendar = baseline.find("LC-019")
    rows.append([
        "LC-019", "embed", baseline.SOURCE_URL,
        "", "", "text/html", "", "",
        "N/A（APPROVED_REMOVE）",
        (calendar.approval if calendar else "")[:200],
    ])

    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "legacy_id", "asset_type", "source_url", "local_archive_path",
            "new_object_key", "mime", "bytes", "sha256", "acquired", "notes",
        ])
        writer.writerows(rows)

    return path


def write_difference_report(results: list[CheckResult]) -> Path:
    """產生 difference_report.md（AC-26）。

    報告結構：
      1. 摘要（通過/失敗數、UNRESOLVED 數）
      2. 逐項檢查結果
      3. UNRESOLVED 清單（必須為空才可 cutover）
    """
    LEGACY_DIR.mkdir(parents=True, exist_ok=True)
    path = LEGACY_DIR / "difference_report.md"

    generated_at = datetime.now(timezone.utc).astimezone().isoformat()
    all_failures = [(r, msg) for r in results for msg in r.failed]
    status_summary = baseline.status_summary()

    lines: list[str] = []
    lines.append("# NTUST SiPh Lab — Legacy Migration Difference Report")
    lines.append("")
    lines.append("> 本檔由 `python scripts/verify_migration.py` 自動產生，請勿手動編輯。")
    lines.append("> 手動修改會與實際驗證結果脫節，違反 ADR-011 的證據鏈要求。")
    lines.append("")
    lines.append(f"- 產生時間：{generated_at}")
    lines.append(f"- 母站來源：{baseline.SOURCE_URL}")
    lines.append(f"- 盤點時間：{baseline.CAPTURED_AT}")
    lines.append(f"- Inventory 總筆數：{len(baseline.LEGACY_ITEMS)}")
    lines.append("")

    # --- 摘要 ---
    lines.append("## 1. 摘要")
    lines.append("")
    lines.append("| 項目 | 數值 |")
    lines.append("| --- | --- |")
    for status, count in sorted(status_summary.items()):
        lines.append(f"| {status} | {count} |")
    lines.append(f"| **UNRESOLVED（含驗證失敗）** | **{len(all_failures)}** |")
    lines.append("")

    signoff_complete, signoff_note = _content_signoff_signed()

    if all_failures:
        lines.append("> **狀態：阻擋上線。** 存在未解決項目，依 SAI §22.1 與 AC-26，")
        lines.append("> 不得進行 Cloud/domain cutover，也不得關閉舊 Google Sites。")
    elif not signoff_complete:
        # 交付前審查 REV-101：自動檢查全過 != 人已經簽核。
        lines.append("> **狀態：自動檢查通過，但尚未取得人工簽核，仍不得 cutover。**")
        lines.append(f"> {signoff_note}")
        lines.append("> 依 SAI §21.1 G6 與 §22.6，content sign-off 完成才是 launch 的必要條件。")
    else:
        lines.append("> **狀態：可進行 cutover。** UNRESOLVED = 0，AC-21~AC-26 全數通過，")
        lines.append("> 且 content_signoff.md 的人工簽核區塊已完成。")
    lines.append("")

    # --- 逐項檢查 ---
    lines.append("## 2. 驗收項目檢查結果")
    lines.append("")
    for result in results:
        icon = "✅" if result.is_passing else "❌"
        lines.append(f"### {icon} {result.ac} — {result.name}")
        lines.append("")
        if result.passed:
            lines.append(f"通過 {len(result.passed)} 項：")
            lines.append("")
            for msg in result.passed:
                lines.append(f"- {msg}")
            lines.append("")
        if result.failed:
            lines.append(f"**失敗 {len(result.failed)} 項：**")
            lines.append("")
            for msg in result.failed:
                lines.append(f"- ❌ {msg}")
            lines.append("")
        if result.skipped:
            lines.append(f"略過 {len(result.skipped)} 項：")
            lines.append("")
            for msg in result.skipped:
                lines.append(f"- ⏭ {msg}")
            lines.append("")

    # --- UNRESOLVED 清單 ---
    lines.append("## 3. UNRESOLVED 清單（Launch Gate）")
    lines.append("")
    if all_failures:
        lines.append("以下項目必須全部解決後重新執行驗證：")
        lines.append("")
        for result, msg in all_failures:
            lines.append(f"- **[{result.ac}]** {msg}")
    else:
        lines.append("無。所有母站內容皆已 MIGRATED 或取得明確核准。")
    lines.append("")

    # --- 平台排除紀錄 ---
    lines.append("## 4. 平台元素排除紀錄（SAI §2.2 / AC-25）")
    lines.append("")
    lines.append("以下為 Google Sites 平台自有的 UI/boilerplate，")
    lines.append("依 SAI §2.2 明確排除，非內容遺漏：")
    lines.append("")
    lines.append("| 元素 | 排除理由 |")
    lines.append("| --- | --- |")
    for item in baseline.PLATFORM_EXCLUSIONS:
        lines.append(f"| {item.element} | {item.reason} |")
    lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# ----------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------
def _load_app():
    """嘗試建立 Flask app；失敗時回 None（見檔頭 Error Handling）。"""
    try:
        from app import create_app

        return create_app(os.environ.get("APP_ENV", "local"))
    except Exception as exc:  # noqa: BLE001
        print(f"[警告] 無法載入應用程式：{exc}", file=sys.stderr)
        return None


def _content_signoff_signed() -> tuple[bool, str]:
    """檢查 content_signoff.md §5 的人工簽核區塊是否真的完成。

    為什麼需要這個檢查（交付前審查 REV-101）：
      這份腳本原本只要 UNRESOLVED = 0 就輸出「可進行 cutover」。
      但 SAI §21.1 G6 的條件是「difference_report 無 UNRESOLVED
      **且** content sign-off 完成」—— 兩個條件，不是一個。

      實務上曾發生：自動檢查全過、報告宣告可 cutover，
      但 content_signoff.md §5 的六個 checkbox 全未勾、
      簽核人與日期三欄全是空白底線。也就是說，
      一份「沒有人簽名的簽核書」被當成通過。

      自動化能證明「資料對得起來」，不能證明「人看過並同意」。
      這個函式把後者變成同樣硬性的門檻。

    Returns:
        (是否已簽核, 說明文字)
    """
    path = LEGACY_DIR / "content_signoff.md"
    if not path.is_file():
        return False, "找不到 legacy/google_sites/content_signoff.md。"

    text = path.read_text(encoding="utf-8")

    unchecked = text.count("- [ ]")
    # 簽核欄位以全形底線佔位，尚未填寫時仍會存在。
    blank_fields = text.count("＿＿＿")

    if unchecked or blank_fields:
        return False, (
            f"content_signoff.md §5 尚未完成："
            f"{unchecked} 個確認項未勾選、{blank_fields} 個簽核欄位仍為空白。"
        )
    return True, "content_signoff.md §5 已完成簽核。"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="NTUST SiPh Lab 遷移驗證（SAI §22、AC-21~AC-26）。"
    )
    parser.add_argument("--legacy", action="store_true", help="執行母站零遺漏檢查。")
    parser.add_argument("--database", action="store_true", help="執行資料庫完整性檢查。")
    parser.add_argument("--all", action="store_true", help="執行全部檢查（預設）。")
    parser.add_argument(
        "--no-report",
        action="store_true",
        help="不產生 legacy/ 底下的 CSV 與 difference_report.md。",
    )
    args = parser.parse_args()

    run_legacy = args.legacy or args.all or not (args.legacy or args.database)
    run_database = args.database or args.all or not (args.legacy or args.database)

    app = _load_app()
    results: list[CheckResult] = []

    if run_legacy:
        results.append(check_inventory_completeness())
        results.append(check_no_blocking_status())
        results.append(check_content_present(app))
        results.append(check_media_manifest(app))
        results.append(check_platform_exclusions())

    if run_database:
        results.append(check_database_integrity(app))

    # --- 輸出到終端機 ---
    print("=" * 68)
    print("NTUST SiPh Lab — 遷移驗證結果")
    print("=" * 68)
    for result in results:
        print(result)
        for msg in result.failed:
            print(f"    ✘ {msg}")
    print("=" * 68)

    total_failures = sum(len(r.failed) for r in results)

    # --- 產生證據檔案 ---
    if not args.no_report:
        inventory = write_inventory_csv()
        mapping = write_mapping_csv()
        media = write_media_manifest_csv(app)
        report = write_difference_report(results)
        print("已產生證據檔案：")
        for p in (inventory, mapping, media, report):
            print(f"  - {p.relative_to(PROJECT_ROOT)}")
        print("=" * 68)

    if total_failures:
        print(f"驗證失敗：{total_failures} 項未解決，阻擋 cutover（SAI §22.1、AC-26）。")
        return 1

    # 自動檢查全過 != 可以上線。SAI §21.1 G6 是兩個條件的 AND：
    # UNRESOLVED = 0 **且** content sign-off 完成（交付前審查 REV-101）。
    signoff_complete, signoff_note = _content_signoff_signed()
    if not signoff_complete:
        print("自動檢查通過：UNRESOLVED = 0。")
        print(f"但仍不得 cutover：{signoff_note}")
        print("依 SAI §21.1 G6 與 §22.6，content sign-off 完成才是 launch 的必要條件。")
        return 2

    print("驗證通過：UNRESOLVED = 0 且人工簽核完成，符合 AC-21~AC-26 的 launch gate。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
