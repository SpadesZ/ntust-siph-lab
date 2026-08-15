#!/usr/bin/env python
# ============================================================
# NTUST SiPh Lab - Google Sites Legacy Seed Script
#
# 上下游：
#   scripts/legacy_baseline.py（母站盤點資料）
#       -> 本腳本 -> PersonService / SettingsService / MediaService
#       -> SQLite（或 PostgreSQL）+ StorageBackend
#       -> scripts/verify_migration.py 驗證結果
#
#   執行方式：
#     flask seed legacy            （透過 app/cli.py）
#     python scripts/seed_from_google_sites.py [--force]
#
# 檔案路徑：
#   scripts/seed_from_google_sites.py
#
# 建立日期：2026-08-14
# 最後重大修改：2026-08-14
# 版本：v1.0
#
# 模組定位與責任邊界：
#   把 SAI §2.1 Legacy Baseline（LC-001~LC-019）的內容寫入資料庫。
#   這是 ADR-011「母站零遺漏遷移」的實際執行者，也是
#   AC-23（教授資料、六項專長、Email、外鏈、四位成員可被找到）
#   的資料來源。
#
#   責任邊界（不得做的事）：
#     - 不得寫入母站不存在的內容（SAI §2.3、§23.1）。
#       例如：學生的英文姓名、研究方向、論文題目、研究成果。
#     - 不得直接操作 ORM 寫入（一律經過 service 層，
#       確保 AuditLog 與交易邊界一致）。
#     - 不得建立管理員帳號（那是 flask admin create）。
#
# 輸入 -> 處理 -> 輸出 Pipeline：
#   legacy_baseline.LEGACY_ITEMS
#     -> 依 legacy_id 尋找既有資料（可重複執行）
#     -> SiteSetting 更新（LC-001, 006~013）
#     -> Person 建立/更新（LC-003~005, 015~018）
#     -> 教授照片上傳（LC-002，含 checksum 驗證）
#     -> 發布（滿足 AC-23）
#     -> 回傳執行摘要
#
# 主要 Function：
#   run_seed(force)           - 主要進入點，回傳摘要字串清單
#   _seed_site_settings()     - LC-001、LC-006~013
#   _seed_professor()         - LC-002~005、LC-012、LC-013
#   _seed_students()          - LC-015~018
#   _upload_legacy_photo()    - LC-002 資產上傳與 checksum 比對
#
# 依賴套件：
#   flask（app context）、app.services.*、app.models.*
#
# 環境變數：
#   透過 app.config：STORAGE_BACKEND、UPLOAD_DIR
#
# 資料庫使用方式：
#   寫入 site_settings、people、audit_logs。
#   透過 service 層執行，因此每筆變更都有稽核紀錄。
#
# Error Handling / Fallback：
#   - 照片檔案不存在時，「不」建立假圖，而是記錄警告並繼續，
#     由 verify_migration 標示為 UNRESOLVED（SAI §23.1 最後一條：
#     Agent 無法取得資產時必須建立 UNRESOLVED issue，
#     不得用空白或任意替代圖靜默略過）。
#   - checksum 不符時中止並報錯 —— 那表示資產被竄改或抓錯檔案。
#
# 特殊機制（冪等性）：
#   本腳本可重複執行。判斷依據是 people.legacy_id 與
#   SiteSetting singleton，而非姓名或 slug ——
#   因為管理者可能已在後台修改姓名或 slug，
#   以 legacy_id 對應才能正確找到同一筆資料而不產生重複。
#
#   預設「不覆寫」管理者已修改的欄位（只填補空值）；
#   --force 才會以母站原文覆寫。這個預設很重要：
#   研究室補完學生研究方向後重跑 seed，不應該把資料清掉。
#
# 已知限制與禁止事項：
#   1. 禁止在此新增任何 ResearchOutput —— 母站沒有研究成果，
#      憑空建立會違反 SAI §2.3 與 §14.3。
#   2. 禁止建立畢業生 —— 母站沒有畢業生資料。
#   3. 禁止把 LC-019（Google Calendar）以任何形式寫入 ——
#      該項目已核准為 APPROVED_REMOVE。
#
# 維護契約：
#   母站資料若有更新，請修改 scripts/legacy_baseline.py，
#   不要直接改本腳本的邏輯。本腳本只負責「把 baseline 寫進 DB」。
#
# 驗證方式：
#   flask seed legacy
#   python scripts/verify_migration.py --legacy
#   pytest tests/test_legacy_migration.py
# ============================================================

from __future__ import annotations

import hashlib
import io
import logging
import os
import sys
from pathlib import Path

# 允許以 `python scripts/seed_from_google_sites.py` 直接執行。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.extensions import db  # noqa: E402
from app.models.mixins import PersonStatus, PublishStatus  # noqa: E402
from app.models.person import Person  # noqa: E402
from app.models.site_setting import SiteSetting  # noqa: E402
from app.services.person_service import PersonService  # noqa: E402
from app.services.settings_service import SettingsService  # noqa: E402
from app.storage import get_storage  # noqa: E402
from scripts import legacy_baseline as baseline  # noqa: E402

logger = logging.getLogger(__name__)

#: 專案根目錄。
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _find_by_legacy_id(legacy_id: str) -> Person | None:
    """依 legacy_id 尋找既有人物（冪等性的依據，見檔頭說明）。"""
    from sqlalchemy import select

    return db.session.scalar(select(Person).where(Person.legacy_id == legacy_id))


def _upload_legacy_photo(person: Person, summary: list[str], force: bool) -> None:
    """上傳 LC-002 教授照片並驗證 checksum（AC-24）。

    行為：
      1. 若人物已有照片且非 force，直接略過（不覆寫管理者上傳的版本）。
      2. 檔案不存在 -> 記錄 UNRESOLVED 警告，不建立替代圖。
      3. checksum 不符 -> 中止並拋錯（表示資產有問題）。
    """
    asset_path = PROJECT_ROOT / baseline.PROFESSOR["photo_asset"]

    if person.photo_path and not force:
        summary.append(f"LC-002 教授照片：已存在（{person.photo_path}），略過。")
        return

    if not asset_path.is_file():
        # SAI §23.1：無法取得資產時必須建立 UNRESOLVED issue，
        # 不得用空白或任意替代圖靜默略過。
        message = (
            f"LC-002 教授照片：UNRESOLVED —— 找不到原始資產 {asset_path}。"
            " 請重新從母站下載並放置於該路徑後再執行一次。"
            " 系統不會使用替代圖片（SAI §23.1）。"
        )
        logger.error(message)
        summary.append(message)
        return

    raw = asset_path.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    expected = baseline.PROFESSOR["photo_sha256"]

    if actual != expected:
        raise RuntimeError(
            f"LC-002 教授照片 checksum 不符。\n"
            f"  預期：{expected}\n  實際：{actual}\n"
            f"  檔案：{asset_path}\n"
            "資產可能已被替換或下載不完整，請重新取得原檔（AC-24）。"
        )

    # 以 FileStorage 介面包裝，走與後台上傳完全相同的處理流程
    # （驗證、重新編碼、移除 metadata）—— 確保 seed 進來的圖片
    # 與管理者上傳的圖片在系統中沒有差別。
    from werkzeug.datastructures import FileStorage

    file_storage = FileStorage(
        stream=io.BytesIO(raw),
        filename="LC-002_professor_photo.jpg",
        content_type="image/jpeg",
    )

    PersonService.attach_photo(
        person,
        file_storage,
        alt_zh=f"{baseline.PROFESSOR['name_zh']}{baseline.PROFESSOR['title_zh']}照片",
    )
    summary.append(
        f"LC-002 教授照片：已上傳（object key={person.photo_path}，"
        f"來源 checksum={expected[:16]}…，AC-24 通過）。"
    )


def _merge_preserving_existing(person: Person, payload: dict) -> tuple[dict, list[str]]:
    """把 baseline payload 與既有資料合併，只填補空欄位。

    為什麼需要這個函式：
      PersonService.update() 採「全量覆寫」語意 —— 傳入的 dict
      就是最終結果，未出現在 dict 中的欄位會被設為 None。
      因此不能只傳「要補的欄位」，必須傳完整 payload，
      但其中「管理者已填寫的欄位」要用現有值而非母站原文。

    Args:
        person: 既有人物。
        payload: 來自 legacy_baseline 的完整欄位。

    Returns:
        (合併後的完整 payload, 實際被填補的欄位名稱清單)

    設計理由（見檔頭「特殊機制（冪等性）」）：
      研究室補完學生研究方向後重跑 seed，不應該把資料清掉。
      因此預設「現有值優先」，只有空值才用母站原文填補。
    """
    #: 這些欄位屬於「排版/展示設定」而非母站內容，
    #: 一律以 baseline 的值為準，不視為使用者資料。
    ALWAYS_FROM_BASELINE = {"skills", "sort_order", "is_featured", "status"}

    merged: dict = {}
    filled: list[str] = []

    for key, baseline_value in payload.items():
        if key in ALWAYS_FROM_BASELINE:
            merged[key] = baseline_value
            continue

        current = getattr(person, key, None)
        if current:
            merged[key] = current
        else:
            merged[key] = baseline_value
            if baseline_value:
                filled.append(key)

    return merged, filled


def _seed_site_settings(force: bool, summary: list[str]) -> SiteSetting:
    """寫入 LC-001（Lab 名稱）、LC-006~011（六項專長）、
    LC-012（Email）、LC-013（NTUST 外鏈）到 SiteSetting。
    """
    setting = SiteSetting.get()

    data: dict = {}

    # LC-001：Lab 名稱。
    if force or not setting.lab_name_zh or setting.lab_name_zh == "NTUST SiPh Lab":
        data["lab_name_zh"] = baseline.LAB_NAME
        data["lab_name_en"] = baseline.LAB_NAME

    # 學校與系所：母站未明列系所，僅由教授學歷可確認學校。
    # 因此只填學校，系所留空由管理者補（不猜測）。
    if force or not setting.university_zh:
        data["university_zh"] = "國立臺灣科技大學"
        data["university_en"] = "National Taiwan University of Science and Technology"

    # LC-012：Email（同時作為站台聯絡信箱）。
    if force or not setting.contact_email:
        data["contact_email"] = baseline.PROFESSOR["email_public"]

    # LC-013：NTUST 外鏈。
    if force or not setting.official_ntust_url:
        data["official_ntust_url"] = baseline.PROFESSOR["external_url"]

    # SEO 預設後綴。
    if force or not setting.default_title_suffix:
        data["default_title_suffix"] = baseline.LAB_NAME

    # LC-006~011：六項專長 -> 首頁與 About 的研究方向。
    # description_zh 一律留空（母站無定義文字，SAI §2.3）。
    if force or not setting.research_focus:
        data["research_focus"] = [
            {
                "title_zh": item["title_zh"],
                "title_en": item["title_en"],
                "description_zh": "",
            }
            for item in baseline.EXPERTISE
        ]

    if data:
        SettingsService.update(data)
        summary.append(
            f"SiteSetting：已寫入 {len(data)} 個欄位群"
            f"（LC-001、LC-006~011、LC-012、LC-013）。"
        )
    else:
        summary.append("SiteSetting：既有內容已完整，未變更（使用 --force 可覆寫）。")

    return SiteSetting.get()


def _seed_professor(force: bool, summary: list[str]) -> Person:
    """建立/更新教授（LC-002~LC-005、LC-012、LC-013）。"""
    prof = baseline.PROFESSOR
    person = _find_by_legacy_id(prof["legacy_id"])

    payload = {
        "name_zh": prof["name_zh"],
        "name_en": prof["name_en"],
        "status": PersonStatus.FACULTY,
        "title_zh": prof["title_zh"],
        "title_en": prof["title_en"],
        "education_zh": prof["education_zh"],
        # 六項專長作為教授的研究焦點（母站的呈現形式）。
        "research_focus_zh": baseline.EXPERTISE_TEXT_ZH,
        "email_public": prof["email_public"],
        "external_url": prof["external_url"],
        "external_url_label": prof["external_url_label"],
        "skills": [item["title_zh"] for item in baseline.EXPERTISE],
        "sort_order": 1,
        "is_featured": True,
    }

    if person is None:
        payload["legacy_id"] = prof["legacy_id"]
        person = PersonService.create(payload)
        summary.append(
            f"LC-003~005 教授：已建立「{person.name_zh}」（/people/{person.slug}）。"
        )
    elif force:
        PersonService.update(person, payload)
        summary.append("LC-003~005 教授：已以母站原文覆寫（--force）。")
    else:
        merged, filled = _merge_preserving_existing(person, payload)
        if filled:
            PersonService.update(person, merged)
            summary.append(
                f"LC-003~005 教授：已補齊 {len(filled)} 個空欄位（{'、'.join(filled)}）。"
            )
        else:
            summary.append("LC-003~005 教授：資料已完整，未變更。")

    _upload_legacy_photo(person, summary, force)

    # 發布（AC-23：教授資料必須可在新站找到）。
    if person.publish_status != PublishStatus.PUBLISHED:
        PersonService.publish(person)
        summary.append(f"　└ 已發布教授頁面：/people/{person.slug}")

    return person


def _seed_students(force: bool, summary: list[str]) -> list[Person]:
    """建立/更新四位碩二生（LC-015~LC-018）。

    重要：只寫入母站確實存在的資料（姓名 + 年級）。
    英文姓名、研究方向、論文題目一律留空，
    並以 legacy_pending_detail=True 標記待補（SAI §2.3）。
    """
    created: list[Person] = []

    for index, student in enumerate(baseline.STUDENTS, start=1):
        person = _find_by_legacy_id(student["legacy_id"])

        payload = {
            "name_zh": student["name_zh"],
            "status": PersonStatus.CURRENT,
            "title_zh": student["title_zh"],
            "sort_order": 10 + index,
        }

        if person is None:
            payload["legacy_id"] = student["legacy_id"]
            # 標記為「母站遷入、欄位待補」——
            # 這個旗標讓 PublishValidator 豁免研究焦點的必填要求，
            # 同時在 Admin Dashboard 持續提醒（2026-08-15 管理者裁示）。
            payload["legacy_pending_detail"] = True
            person = PersonService.create(payload)
            summary.append(
                f"{student['legacy_id']} 成員：已建立「{person.name_zh}」"
                f"（/people/{person.slug}，欄位待補）。"
            )
        elif force:
            PersonService.update(person, {**payload, "legacy_pending_detail": True})
            summary.append(f"{student['legacy_id']} 成員：已以母站原文覆寫（--force）。")
        else:
            summary.append(f"{student['legacy_id']} 成員：已存在，未變更。")

        # 發布（AC-23：四位成員姓名必須能在新站被找到）。
        if person.publish_status != PublishStatus.PUBLISHED:
            PersonService.publish(person)
            summary.append(f"　└ 已發布：/people/{person.slug}")

        created.append(person)

    return created


def run_seed(force: bool = False) -> list[str]:
    """執行母站內容匯入。

    Args:
        force: True 時以母站原文覆寫既有欄位。
               預設 False 只填補空值（見檔頭「特殊機制（冪等性）」）。

    Returns:
        執行摘要字串清單，供 CLI 顯示。

    Raises:
        RuntimeError: LC-002 資產 checksum 不符。
    """
    summary: list[str] = []

    summary.append(f"母站來源：{baseline.SOURCE_URL}")
    summary.append(f"盤點時間：{baseline.CAPTURED_AT}")
    summary.append("")

    _seed_site_settings(force=force, summary=summary)
    _seed_professor(force=force, summary=summary)
    _seed_students(force=force, summary=summary)

    summary.append("")
    summary.append(
        f"LC-019 Google Calendar：APPROVED_REMOVE（2026-08-15 管理者核准，"
        "未寫入資料庫，核准紀錄見 legacy/google_sites/content_signoff.md）。"
    )
    summary.append("")
    summary.append("未匯入任何研究成果或畢業生 —— 母站不存在該類資料，")
    summary.append("依 SAI §2.3 不得由系統推測產生，需由研究室提供後於後台新增。")

    return summary


def main() -> int:
    """命令列進入點。"""
    import argparse

    parser = argparse.ArgumentParser(
        description="匯入 NTUST SiPh Lab 母站 Legacy Baseline（LC-001~LC-019）。"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="以母站原文覆寫既有欄位（預設只填補空值，不覆寫後台修改）。",
    )
    args = parser.parse_args()

    from app import create_app

    app = create_app(os.environ.get("APP_ENV", "local"))
    with app.app_context():
        try:
            summary = run_seed(force=args.force)
        except Exception as exc:  # noqa: BLE001
            db.session.rollback()
            print(f"匯入失敗：{exc}", file=sys.stderr)
            return 1

    print("母站內容匯入完成：")
    for line in summary:
        print(f"  {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
