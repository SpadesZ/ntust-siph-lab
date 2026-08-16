# ============================================================
# NTUST SiPh Lab - Legacy Baseline Data (LC-001 ~ LC-019)
#
# 上下游：
#   本檔（母站盤點資料的單一真實來源）
#       -> scripts/seed_from_google_sites.py（寫入資料庫）
#       -> scripts/verify_migration.py（差異比對 / difference report）
#       -> tests/test_legacy_migration.py（AC-21~AC-26 驗收）
#       -> legacy/google_sites/migration_inventory.csv（產生清冊）
#
# 檔案路徑：
#   scripts/legacy_baseline.py
#
# 建立日期：2026-08-14
# 最後重大修改：2026-08-14
# 版本：v1.0
#
# 模組定位與責任邊界：
#   SAI §2.1 Legacy Baseline Inventory 的可執行版本。
#   ADR-011（Legacy Content Preservation Gate）要求母站所有
#   確認有效的內容都必須建立 inventory 與 new-site mapping，
#   且 §22.1 規定 UNRESOLVED 必須為 0 才能上線（AC-22）。
#
#   為什麼把盤點資料放在程式碼而不是只放 CSV：
#     AC-21~AC-26 需要「以程式驗證」每一筆 LC 項目都有
#     source、type、target、status，且沒有空白欄位。
#     若資料只存在 CSV，驗證腳本會因為手動編輯造成的
#     欄位錯位或拼字差異而誤判通過。以 Python 結構定義後，
#     CSV 由本檔產生，兩者不可能不一致。
#
#   責任邊界（不得做的事）：
#     - 不得在此寫入資料庫（那是 seed script）。
#     - 不得加入母站不存在的內容（SAI §2.3、§23.1：
#       Agent 不得猜測填入）。
#     - 不得未經核准就把項目標為 APPROVED_REMOVE。
#
# 輸入 -> 處理 -> 輸出 Pipeline：
#   輸入：2026-08-14 與 2026-08-15 對母站的兩次抓取結果
#   處理：以 LegacyItem 結構化，附上 target 與 status
#   輸出：LEGACY_ITEMS（供 seed / verify / test 使用）
#
# 主要 Class / 常數：
#   LegacyItem        - 單一 legacy 項目
#   LEGACY_ITEMS      - LC-001 ~ LC-019 完整清單
#   SOURCE_URL        - 母站網址
#   CAPTURED_AT       - 盤點時間（ISO-8601）
#   PLATFORM_EXCLUSIONS - SAI §2.2 明確不搬遷的平台元素
#   PROFESSOR / STUDENTS / EXPERTISE - 供 seed 使用的結構化資料
#
# 依賴套件：
#   標準庫 dataclasses / typing。刻意零外部相依，
#   讓驗證腳本可在最小環境執行。
#
# 環境變數：無。
# 資料庫使用方式：無。
#
# Error Handling / Fallback：
#   本檔為純資料宣告，無執行期錯誤路徑。
#   資料正確性由 tests/test_legacy_migration.py 驗證。
#
# 特殊機制（核准紀錄）：
#   status 為 APPROVED_REWRITE 或 APPROVED_REMOVE 的項目
#   「必須」填寫 approval 欄位（核准人、日期、理由），
#   這是 SAI §22.2 的條件式必填規則，
#   由 tests/test_legacy_migration.py 強制檢查。
#
# 已知限制與禁止事項：
#   1. 禁止在未取得教授/管理者核准的情況下，把任何項目
#      從 MIGRATE 改為 APPROVED_REMOVE（ADR-011 / §23.1）。
#   2. 禁止刪除任何 LC 項目 —— 即使該內容已不再需要，
#      也必須保留紀錄並標示核准過的處理狀態。
#   3. 四位碩二生的英文姓名、研究方向、論文題目在母站
#      皆不存在，因此本檔不提供這些欄位（§2.3）。
#
# 維護契約：
#   1. 母站若有更新，必須重新抓取並更新 CAPTURED_AT，
#      同時在 legacy/google_sites/source_snapshot.md 記錄。
#   2. 任何 status 變更都必須同時填寫 approval。
#   3. 修改本檔後必須重新執行：
#        python scripts/verify_migration.py --legacy
#      以重新產生 difference_report.md。
#
# 驗證方式：
#   pytest tests/test_legacy_migration.py
#   python scripts/verify_migration.py --legacy
# ============================================================

from __future__ import annotations

from dataclasses import dataclass, field

#: 母站網址（SAI §2、[S1]）。
SOURCE_URL = "https://sites.google.com/view/ntust-siph-lab/"

#: 盤點時間。SAI 文件本身於 2026-08-14 完成盤點；
#: 本次實作於 2026-08-15 重新抓取驗證，兩次結果一致。
CAPTURED_AT = "2026-08-15T00:00:00+08:00"

#: SAI 文件所記載的原始盤點日期。
SAI_BASELINE_DATE = "2026-08-14"


# ----------------------------------------------------------------------
# 遷移狀態（與 app.models.mixins.MigrationStatus 對應）
#
# 這裡重複宣告字面值而非 import app.models，理由：
#   verify_migration.py 需要能在「沒有建立 Flask app」的情況下
#   執行 legacy 檢查（例如 CI 的快速檢查步驟）。
#   tests/test_legacy_migration.py 會驗證兩處的值完全一致，
#   因此不會發生漂移。
# ----------------------------------------------------------------------
STATUS_MIGRATED = "MIGRATED"
STATUS_APPROVED_REWRITE = "APPROVED_REWRITE"
STATUS_APPROVED_REMOVE = "APPROVED_REMOVE"
STATUS_REVIEW_REQUIRED = "REVIEW_REQUIRED"
STATUS_UNRESOLVED = "UNRESOLVED"
STATUS_DISCOVERED = "DISCOVERED"


@dataclass(frozen=True)
class LegacyItem:
    """單一母站內容項目（SAI §22.2 migration_inventory.csv 最低欄位）。

    frozen=True：盤點結果是「已發生的事實」，
    不應在程式執行期間被修改。
    """

    legacy_id: str
    source_url: str
    captured_at: str
    content_type: str          # text / person / image / external_link / embed / section
    source_value: str          # 原始可見文字或資產描述
    target_entity: str         # SiteSetting / Person / ResearchOutput / Page / Asset
    target_url_or_field: str   # 新站 URL 或 DB 欄位
    status: str
    verification: str          # exact match / HTTP check / checksum / 人工核對
    approval: str = ""         # rewrite/remove 時必填：核准人、日期、理由
    notes: str = ""

    def requires_approval(self) -> bool:
        """此狀態是否必須有核准紀錄（SAI §22.2 條件式必填）。"""
        return self.status in (STATUS_APPROVED_REWRITE, STATUS_APPROVED_REMOVE)

    def blocks_launch(self) -> bool:
        """此項目是否阻擋正式上線（SAI §22.1、AC-22）。"""
        return self.status not in (
            STATUS_MIGRATED,
            STATUS_APPROVED_REWRITE,
            STATUS_APPROVED_REMOVE,
        )


# ----------------------------------------------------------------------
# 教授資料（LC-002 ~ LC-013）
#
# 全部逐字取自母站，未經改寫。英文姓名 "Chun-Liang Yang" 出現在
# 母站的英文標示中，因此屬於母站既有內容，非 Agent 推測。
# ----------------------------------------------------------------------
PROFESSOR = {
    "legacy_id": "LC-003",
    "name_zh": "楊淳良",
    "name_en": "Chun-Liang Yang",
    "title_zh": "副教授",
    "title_en": "Associate Professor",
    "education_zh": "國立臺灣科技大學電子工程博士",
    "email_public": "yangcl@mail.ntust.edu.tw",
    "external_url": "https://innc.ntust.edu.tw/p/412-1111-12071.php?Lang=zh-tw",
    "external_url_label": "NTUST Website",
    #: LC-002 教授頁首圖片。檔案已下載並保存於
    #: legacy/google_sites/assets/，checksum 記錄於 media_manifest.csv。
    "photo_asset": "legacy/google_sites/assets/LC-002_professor_photo.jpg",
    "photo_sha256": "1d46458f9eace22ef75c9436995216ab12b1240a4dfa2edca3622ebf68221a1f",
    "photo_source_url": (
        "https://lh3.googleusercontent.com/sitesv/AG8ngQWhYT3sStHw2D7sQACr48AsQ0Bqa"
        "PJCVGWCgOQ3msTsT8WYE3trBu0SakHjobHnusZiWteIMBQjVnuFsnfJAGqYIPWMrFbOYIYnJ5b"
        "AUyoqx35C1yK8AXohFdidSjJGd6PLSUFJIJXe4eZOHcGgY8GSR7hMqVjnCUvNNnxJgkJZ92HLU"
        "LmPQFpf2O9csFTP0kLsfl7P7sBX5NVjDPzjto2TPz4w373IdRY4VRignH0=w1280"
    ),
}

#: LC-006 ~ LC-011：教授的六項研究專長，順序與母站一致。
#: 英文譯名為通用學術術語對照，僅作為輔助顯示；
#: 中文為母站原文，是 exact match 比對的基準（SAI §22.5）。
#: description 一律留空 —— 母站沒有提供各專長的定義文字，
#: 依 SAI §2.3 不得由 Agent 猜測填入（AC-17 的精神）。
EXPERTISE = [
    {"legacy_id": "LC-006", "title_zh": "光電感測技術", "title_en": "Optoelectronic Sensing Technology"},
    {"legacy_id": "LC-007", "title_zh": "矽光子技術", "title_en": "Silicon Photonics Technology"},
    {"legacy_id": "LC-008", "title_zh": "光通道效能監視", "title_en": "Optical Performance Monitoring"},
    {"legacy_id": "LC-009", "title_zh": "光通訊系統", "title_en": "Optical Communication Systems"},
    {"legacy_id": "LC-010", "title_zh": "物聯網平台", "title_en": "IoT Platform"},
    {"legacy_id": "LC-011", "title_zh": "人工智慧技術應用", "title_en": "Applied Artificial Intelligence"},
]

#: 六項專長合併為教授的 research_focus 文字（母站呈現形式）。
EXPERTISE_TEXT_ZH = "、".join(item["title_zh"] for item in EXPERTISE)

# ----------------------------------------------------------------------
# 四位碩二生（LC-015 ~ LC-018）
#
# 母站只提供中文姓名與「碩二生」身分。
# SAI §2.1 標註「MIGRATE EXACT；其餘欄位待確認」，
# §2.3 明文禁止 Agent 猜測補入英文名、研究方向或論文題目。
#
# 依 2026-08-15 管理者裁示（見 content_signoff.md）：
#   四人以 published 上架以滿足 AC-23（姓名必須能被找到），
#   缺漏欄位以 legacy_pending_detail=True 標記，
#   由 Admin Dashboard 持續提醒補齊。
# ----------------------------------------------------------------------
STUDENTS = [
    {"legacy_id": "LC-015", "name_zh": "陳泓序", "title_zh": "碩二生"},
    {"legacy_id": "LC-016", "name_zh": "謝卓雅", "title_zh": "碩二生"},
    {"legacy_id": "LC-017", "name_zh": "鍾宇辰", "title_zh": "碩二生"},
    {"legacy_id": "LC-018", "name_zh": "陳志翰", "title_zh": "碩二生"},
]

#: 母站的 Lab 名稱（LC-001）。
LAB_NAME = "NTUST SiPh Lab"


# ----------------------------------------------------------------------
# 完整 inventory（LC-001 ~ LC-019）
# ----------------------------------------------------------------------
LEGACY_ITEMS: list[LegacyItem] = [
    LegacyItem(
        legacy_id="LC-001",
        source_url=SOURCE_URL,
        captured_at=CAPTURED_AT,
        content_type="text",
        source_value=LAB_NAME,
        target_entity="SiteSetting",
        target_url_or_field="site_settings.lab_name_zh / lab_name_en（Header、首頁、metadata）",
        status=STATUS_MIGRATED,
        verification="exact text match（tests/test_legacy_migration.py::test_lab_name_migrated）",
    ),
    LegacyItem(
        legacy_id="LC-002",
        source_url=PROFESSOR["photo_source_url"],
        captured_at=CAPTURED_AT,
        content_type="image",
        source_value="教授頁首圖片（Google Sites 圖片資產，400x468 JPEG，40847 bytes）",
        target_entity="Asset + Person",
        target_url_or_field="people.photo_path（/about、/members、/people/chun-liang-yang）",
        status=STATUS_MIGRATED,
        verification=(
            "checksum sha256="
            + PROFESSOR["photo_sha256"]
            + "；原檔保存於 legacy/google_sites/assets/LC-002_professor_photo.jpg"
        ),
        notes="原檔已成功下載，未使用替代圖，符合 AC-24。",
    ),
    LegacyItem(
        legacy_id="LC-003",
        source_url=SOURCE_URL,
        captured_at=CAPTURED_AT,
        content_type="person",
        source_value="Chun-Liang Yang / 楊淳良",
        target_entity="Person",
        target_url_or_field="people.name_zh / name_en（/people/chun-liang-yang）",
        status=STATUS_MIGRATED,
        verification="exact text match",
    ),
    LegacyItem(
        legacy_id="LC-004",
        source_url=SOURCE_URL,
        captured_at=CAPTURED_AT,
        content_type="text",
        source_value="副教授 / Associate Professor",
        target_entity="Person",
        target_url_or_field="people.title_zh / title_en",
        status=STATUS_MIGRATED,
        verification="exact text match",
    ),
    LegacyItem(
        legacy_id="LC-005",
        source_url=SOURCE_URL,
        captured_at=CAPTURED_AT,
        content_type="text",
        source_value="國立臺灣科技大學電子工程博士",
        target_entity="Person",
        target_url_or_field="people.education_zh",
        status=STATUS_MIGRATED,
        verification="exact text match",
    ),
    # LC-006 ~ LC-011：六項專長，逐項列出（SAI §22.3「六項集合比對，不可漏項」）
    *[
        LegacyItem(
            legacy_id=item["legacy_id"],
            source_url=SOURCE_URL,
            captured_at=CAPTURED_AT,
            content_type="text",
            source_value=item["title_zh"],
            target_entity="SiteSetting + Person",
            target_url_or_field=(
                "site_settings.research_focus_json（/、/about 研究方向）"
                " + people.research_focus_zh（/people/chun-liang-yang）"
            ),
            status=STATUS_MIGRATED,
            verification="exact set comparison（六項皆須存在且順序一致）",
        )
        for item in EXPERTISE
    ],
    LegacyItem(
        legacy_id="LC-012",
        source_url=SOURCE_URL,
        captured_at=CAPTURED_AT,
        content_type="text",
        source_value=PROFESSOR["email_public"],
        target_entity="Person + SiteSetting",
        target_url_or_field="people.email_public + site_settings.contact_email（/join、footer）",
        status=STATUS_MIGRATED,
        verification="exact text match（小寫正規化後比對）",
    ),
    LegacyItem(
        legacy_id="LC-013",
        source_url=PROFESSOR["external_url"],
        captured_at=CAPTURED_AT,
        content_type="external_link",
        source_value="NTUST Website -> " + PROFESSOR["external_url"],
        target_entity="Person + SiteSetting",
        target_url_or_field="people.external_url + site_settings.official_ntust_url（footer、/about）",
        status=STATUS_MIGRATED,
        verification="HTTP check 200（2026-08-15 驗證通過，URL 未變更）",
    ),
    LegacyItem(
        legacy_id="LC-014",
        source_url=SOURCE_URL,
        captured_at=CAPTURED_AT,
        content_type="section",
        source_value="研究室成員（section 概念）",
        target_entity="Page",
        target_url_or_field="/members（並擴充為 /alumni 與 /people/<slug> 個人頁）",
        status=STATUS_MIGRATED,
        verification="頁面存在且列出全部四位成員（MIGRATE + EXPAND）",
        notes="SAI §2.1 標示為 MIGRATE + EXPAND：新站新增個人詳細頁與畢業生分流。",
    ),
    # LC-015 ~ LC-018：四位碩二生
    *[
        LegacyItem(
            legacy_id=student["legacy_id"],
            source_url=SOURCE_URL,
            captured_at=CAPTURED_AT,
            content_type="person",
            source_value=f"碩二生 {student['name_zh']}",
            target_entity="Person(status=current)",
            target_url_or_field="people.name_zh（/members 與個人頁）",
            status=STATUS_MIGRATED,
            verification="exact text match（四筆姓名皆須存在於 /members）",
            notes=(
                "母站僅提供姓名與年級。英文姓名、研究方向、論文題目母站不存在，"
                "依 SAI §2.3 不得推測填入；已以 legacy_pending_detail 標記待補，"
                "並經 2026-08-16 管理者裁示同意先行發布"
                "（委託人理由：後台管理員可自行補齊；"
                "見 docs/adr/ADR-012-legacy-pending-publish-exemption.md）。"
            ),
        )
        for student in STUDENTS
    ],
    LegacyItem(
        legacy_id="LC-019",
        source_url=SOURCE_URL,
        captured_at=CAPTURED_AT,
        content_type="embed",
        source_value="Google Calendar 嵌入：台灣假日日曆（agenda 檢視）",
        target_entity="—（不搬遷）",
        target_url_or_field="—（經核准不搬遷，新站無對應位置）",
        status=STATUS_APPROVED_REMOVE,
        verification="核准紀錄存於 legacy/google_sites/content_signoff.md",
        approval=(
            "核准人：研究室管理者（本專案委託人）｜核准日期：2026-08-16｜"
            "核准依據：委託人於 2026-08-16 交付前審查的回覆中，就本項目明確表示"
            "「沒有很需要」並同意移除（原文與脈絡見 content_signoff.md §2.1）。｜"
            "委託人理由：非研究室必要內容。｜"
            "技術補充（審查者提供供委託人參考，非委託人本人陳述）："
            "台灣假日日曆屬第三方通用行事曆，與 SAI §3 定義的四類訪客目標皆無關；"
            "且將是全站唯一的第三方 iframe，需為其放寬現行 CSP。｜"
            "決策依 SAI §22.1 由管理者明確批准，非 Agent 自行省略（ADR-011 / §23.1）。"
        ),
        notes="AC-25 要求本項必須有明確 MIGRATED 或 APPROVED_REMOVE 決策，已滿足。",
    ),
]


# ----------------------------------------------------------------------
# SAI §2.2：明確不屬於 Lab 內容的 Google Sites 平台元素
#
# 這些項目「不納入」LEGACY_ITEMS，但必須留下排除紀錄，
# 以證明它們是「依 exclusion list 排除」而非「遺漏」（AC-25）。
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class PlatformExclusion:
    """Google Sites 平台 UI/boilerplate 的排除紀錄。"""

    element: str
    reason: str


PLATFORM_EXCLUSIONS: list[PlatformExclusion] = [
    PlatformExclusion(
        element="Search this site",
        reason="Google Sites 平台導覽/UI，不是 Lab 內容（SAI §2.2）。",
    ),
    PlatformExclusion(
        element="Skip to main content",
        reason="Google Sites 平台導覽/UI；新站以自有 skip link 取代（SAI §2.2）。",
    ),
    PlatformExclusion(
        element="Skip to navigation",
        reason="Google Sites 平台導覽/UI（SAI §2.2）。",
    ),
    PlatformExclusion(
        element="Page updated",
        reason="平台 footer/boilerplate；新站以 updated_at 與 last-updated 顯示取代（SAI §2.2）。",
    ),
    PlatformExclusion(
        element="Google Sites",
        reason="平台品牌標示；新站以自有 footer 取代（SAI §2.2）。",
    ),
    PlatformExclusion(
        element="Report abuse",
        reason="平台功能連結，非 Lab 內容（SAI §2.2）。",
    ),
]


# ----------------------------------------------------------------------
# 彙總輔助
# ----------------------------------------------------------------------
def status_summary() -> dict[str, int]:
    """統計各狀態的項目數（供 content_signoff.md 與驗證腳本使用）。"""
    summary: dict[str, int] = {}
    for item in LEGACY_ITEMS:
        summary[item.status] = summary.get(item.status, 0) + 1
    return summary


def blocking_items() -> list[LegacyItem]:
    """回傳所有阻擋上線的項目（SAI §22.1、AC-22：必須為空）。"""
    return [item for item in LEGACY_ITEMS if item.blocks_launch()]


def find(legacy_id: str) -> LegacyItem | None:
    """依 legacy_id 取得項目。"""
    for item in LEGACY_ITEMS:
        if item.legacy_id == legacy_id:
            return item
    return None
