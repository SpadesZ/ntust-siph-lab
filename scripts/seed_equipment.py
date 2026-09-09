# ============================================================
# NTUST SiPh Lab - 設備／設施初始資料匯入
#
# 上下游：
#   flask seed equipment -> app/cli.py -> 本檔 run_seed()
#       -> models/equipment.py
#       -> /equipment 公開頁（發布後才會顯示）
#
# 檔案路徑：
#   scripts/seed_equipment.py
#
# 建立日期：2026-09-09 / 版本：v1.0
#
# 模組定位與責任邊界：
#   把「有公開出處可查證」的設備資訊建立為 draft 記錄，供管理者
#   逐筆確認後發布。
#
# 資料來源與其限制（重要）：
#   全部來自台科大官方網站的一篇新聞稿（先進半導體科技研究所成立
#   報導，見 SOURCE_URL）。該報導列出的是**華夏校區半導體創新與
#   應用研究中心**的設備，不是楊淳良實驗室的自有資產 —— 因此一律
#   標記為 ownership=institute 或 shared，絕不標成 lab。
#
#   實驗室自有設備（ownership=lab）在本腳本中「刻意沒有任何一筆」。
#   理由：查遍舊站（Google Sites）、產學創新學院、HiSiPIC 研發中心、
#   電子工程系研究實驗室列表與多輪關鍵字搜尋，網路上不存在這間
#   實驗室的自有設備清單。唯一能填的方式是「照矽光子實驗室通常
#   會有什麼」去推測 —— 那會在實驗室官網上憑空生出機台，而看到
#   它的人正是在決定要不要來報考的學生。該層級只能由教授提供。
#
# 為什麼全部是 draft：
#   即使有出處，「報導提到中心採購了這些設備」與「這些設備現在
#   可用、且本實驗室成員用得到」是兩件事，後者只有實驗室自己知道。
#   draft 讓資料先進系統但不對外，管理者確認後再發布。
#
# 主要 Function：
#   run_seed(force=False) - 匯入並回傳摘要行
#
# 冪等性：
#   以 slug 判斷是否已存在。預設跳過既有記錄（避免覆蓋管理者的
#   修改）；--force 時更新描述類欄位，但**不動 publish_status**，
#   否則會把管理者已發布的項目打回草稿。
#
# 驗證方式：
#   pytest tests/test_equipment.py
#   flask seed equipment && flask seed equipment  # 第二次應全部跳過
# ============================================================

from __future__ import annotations

from app.extensions import db
from app.models.equipment import Equipment
from app.models.mixins import EquipmentCategory, EquipmentOwnership, PublishStatus

#: 唯一資料來源：台科大官網新聞稿。
SOURCE_URL = "https://www.ntust.edu.tw/p/406-1000-132465,r167.php?Lang=zh-tw"
SOURCE_NOTE = "臺灣科技大學官方網站新聞稿〈結合產業資源與前瞻設備 臺科大成立半導體研究所布局矽光子與先進封裝領域〉"

#: 設備所在單位。報導明確指出這些設備設置於此。
CENTER_ZH = "臺科大華夏校區 半導體創新與應用研究中心"

#: 待匯入項目。
#:
#: name_en 多數留空：新聞稿是中文的，官方英文名稱未知。自行翻譯
#: 會產生一個查不到出處的名稱，留空只是資料不完整（publish
#: validator 會給 warning 提醒補上），比捏造一個好。
EQUIPMENT_SEED: list[dict] = [
    {
        "slug": "siph-automated-measurement",
        "name_zh": "矽光子自動化測量設備",
        "name_en": None,
        "category": EquipmentCategory.MEASUREMENT,
        "ownership": EquipmentOwnership.INSTITUTE,
        "description_zh": "用於矽光子元件與晶片的自動化量測，由國科會晶創計畫建置。",
        "location_zh": CENTER_ZH,
        "sort_order": 10,
    },
    {
        "slug": "siph-automated-packaging",
        "name_zh": "矽光子自動化封裝設備",
        "name_en": None,
        "category": EquipmentCategory.PACKAGING,
        "ownership": EquipmentOwnership.INSTITUTE,
        "description_zh": "用於矽光子晶片的自動化封裝製程，由國科會晶創計畫建置。",
        "location_zh": CENTER_ZH,
        "sort_order": 20,
    },
    {
        "slug": "semiconductor-surface-inspection",
        "name_zh": "半導體表面檢測設備",
        "name_en": None,
        "category": EquipmentCategory.INSPECTION,
        "ownership": EquipmentOwnership.INSTITUTE,
        "description_zh": "用於晶圓表面品質檢測。",
        "location_zh": CENTER_ZH,
        "sort_order": 30,
    },
    {
        "slug": "fiber-optical-coherence-tomography",
        "name_zh": "光纖型光學同調斷層掃描系統",
        "name_en": "Fiber-based Optical Coherence Tomography System",
        "category": EquipmentCategory.INSPECTION,
        "ownership": EquipmentOwnership.INSTITUTE,
        "description_zh": "用於檢查晶片內部缺陷，包含凹痕、微裂縫與空隙。",
        "location_zh": CENTER_ZH,
        "sort_order": 40,
    },
    {
        "slug": "optical-fiber-array",
        "name_zh": "光纖陣列",
        # 這個英文名直接出現在報導中，不是自行翻譯。
        "name_en": "Optical Fiber Array",
        "category": EquipmentCategory.COMPONENT,
        "ownership": EquipmentOwnership.INSTITUTE,
        "description_zh": "用於影像分析。",
        "location_zh": CENTER_ZH,
        "sort_order": 50,
    },
    {
        "slug": "tsri-equipment-sharing-platform",
        "name_zh": "國研院臺灣半導體研究中心（TSRI）設備共享平台",
        # TSRI 為該中心官方英文簡稱。
        "name_en": "Taiwan Semiconductor Research Institute (TSRI) Equipment Sharing Platform",
        "category": EquipmentCategory.OTHER,
        "ownership": EquipmentOwnership.SHARED,
        "description_zh": (
            "矽光子重點設備可透過 TSRI 設備共享平台預約使用，"
            "開放全台產學研團隊申請。"
        ),
        "location_zh": "國家實驗研究院臺灣半導體研究中心",
        "sort_order": 60,
    },
]

#: --force 時允許覆蓋的欄位。刻意不含 publish_status 與 is_featured
#: —— 那兩個代表管理者的決定，不該被重跑 seed 洗掉。
_UPDATABLE = (
    "name_zh", "name_en", "category", "ownership",
    "description_zh", "location_zh", "sort_order",
    "source_note", "source_url",
)


def run_seed(force: bool = False) -> list[str]:
    """匯入設備 draft 資料，回傳摘要行。"""
    created = 0
    updated = 0
    skipped = 0
    summary: list[str] = []

    for row in EQUIPMENT_SEED:
        data = dict(row)
        data["source_note"] = SOURCE_NOTE
        data["source_url"] = SOURCE_URL

        existing = db.session.query(Equipment).filter_by(slug=data["slug"]).first()

        if existing is None:
            item = Equipment(**data, publish_status=PublishStatus.DRAFT)
            db.session.add(item)
            created += 1
            continue

        if not force:
            skipped += 1
            continue

        for field in _UPDATABLE:
            if field in data:
                setattr(existing, field, data[field])
        updated += 1

    summary.append(f"新增 {created} 筆（狀態：draft）")
    if updated:
        summary.append(f"更新 {updated} 筆")
    if skipped:
        summary.append(f"跳過 {skipped} 筆（已存在，未加 --force）")
    summary.append(f"資料來源：{SOURCE_URL}")
    summary.append(
        "注意：全部為所屬中心／共享平台設施，非實驗室自有。"
        "實驗室自有設備需由教授提供後另行建立。"
    )
    summary.append("這些項目在後台確認並發布前，不會出現在公開頁。")
    return summary
