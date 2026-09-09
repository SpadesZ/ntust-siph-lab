# ============================================================
# NTUST SiPh Lab - Equipment Model
#
# 上下游：
#   models/mixins.py (TimestampMixin / PublishStatus /
#       EquipmentOwnership / EquipmentCategory)
#     -> models/equipment.py（本檔）
#     -> repositories/equipment.py 依 publish_status 與 ownership 查詢
#     -> blueprints/public/routes.py::equipment_index
#     -> blueprints/admin/routes.py 的 CRUD
#
# 檔案路徑：
#   app/models/equipment.py
#
# 建立日期：2026-09-09 / 版本：v1.0
#
# 模組定位與責任邊界：
#   實驗室設備／可用研究設施的單一資料表。前台 /equipment 依
#   ownership 分三組呈現，後台可逐台維護。
#
#   責任邊界（不得做的事）：
#     - 不得在此查詢或組 SEO 字串（那是 repository 與 SEOService）。
#     - 不得放置「推測的」設備。見下方 source_note 說明。
#
# 為什麼需要 source_note / source_url：
#   這張表的資料有兩種來源：教授提供，或從公開資料整理。後者必須
#   能被追溯 —— 這是實驗室官網，設備清單是學生選實驗室時的實質
#   依據，寫上一台實際不存在的機台，代價由來報考的人承擔。
#   因此非 LAB 層級的項目在發布前必須有出處（見
#   PublishValidator.validate_equipment）。
#
# 輸入 -> 處理 -> 輸出：
#   Admin 表單 / seed 腳本 -> 本 model -> repository -> 公開頁
#
# 主要 Class：
#   Equipment - 設備／設施單筆記錄
#
# 資料庫使用方式：
#   VARCHAR + CheckConstraint 表達 category / ownership /
#   publish_status（SAI §10.2：禁止 native ENUM，兩種 DB 行為需一致）。
#
# 已知限制與禁止事項：
#   1. 禁止在此表放入未經確認的「本實驗室設備」（ownership=lab）。
#      網路上查不到這間實驗室的自有設備清單，該層級一律等教授提供。
#   2. 新增 category / ownership 值時必須同步更新 mixins 常數、
#      Alembic CheckConstraint 與 admin form choices（見 mixins 維護契約）。
#
# 驗證方式：
#   pytest tests/test_equipment.py
# ============================================================

from __future__ import annotations

from sqlalchemy import Boolean, CheckConstraint, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db
from app.models.mixins import (
    EquipmentCategory,
    EquipmentOwnership,
    PublishStatus,
    TimestampMixin,
)


class Equipment(TimestampMixin, db.Model):
    """一台設備或一項可使用的研究設施。"""

    __tablename__ = "equipment"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # slug：目前沒有詳細頁，用於前台的 #錨點，讓單一設備可被連結分享。
    slug: Mapped[str] = mapped_column(String(160), nullable=False, unique=True, index=True)

    name_zh: Mapped[str | None] = mapped_column(String(255), nullable=True)
    name_en: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # 型號與製造商多數情況會是空的 —— 公開新聞稿通常只給設備名稱。
    # 留空比填一個猜測的型號好。
    model_number: Mapped[str | None] = mapped_column(String(160), nullable=True)
    manufacturer: Mapped[str | None] = mapped_column(String(160), nullable=True)

    category: Mapped[str] = mapped_column(
        String(30), nullable=False, default=EquipmentCategory.OTHER
    )
    ownership: Mapped[str] = mapped_column(
        String(30), nullable=False, default=EquipmentOwnership.LAB
    )

    # 用途：這台機器拿來做什麼（給學生看的，不是規格書）。
    description_zh: Mapped[str | None] = mapped_column(Text, nullable=True)
    description_en: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 規格：關鍵參數。刻意用自由文字而非結構化欄位 —— 不同類型的
    # 設備關鍵參數完全不同（波長範圍 vs 解析度 vs 通道數），
    # 硬要結構化只會得到一堆空欄位。
    specs_zh: Mapped[str | None] = mapped_column(Text, nullable=True)
    specs_en: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 所在位置（例如「華夏校區半導體創新與應用研究中心」）。
    # 對 institute / shared 層級特別重要：學生需要知道要去哪裡用。
    location_zh: Mapped[str | None] = mapped_column(String(255), nullable=True)
    location_en: Mapped[str | None] = mapped_column(String(255), nullable=True)

    photo_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    photo_alt_zh: Mapped[str | None] = mapped_column(String(220), nullable=True)
    photo_alt_en: Mapped[str | None] = mapped_column(String(220), nullable=True)

    # 出處。見檔頭「為什麼需要 source_note / source_url」。
    source_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    is_featured: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=100)

    publish_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=PublishStatus.DRAFT, index=True
    )

    __table_args__ = (
        CheckConstraint(
            "category IN ('measurement', 'packaging', 'inspection', 'source', "
            "'computing', 'component', 'other')",
            name="ck_equipment_category",
        ),
        CheckConstraint(
            "ownership IN ('lab', 'institute', 'shared')",
            name="ck_equipment_ownership",
        ),
        CheckConstraint(
            "publish_status IN ('draft', 'published', 'archived')",
            name="ck_equipment_publish_status",
        ),
        # 前台的唯一查詢形態：依 publish_status 過濾後，按 ownership
        # 分組再依 sort_order 排列。
        Index("ix_equipment_publish_ownership", "publish_status", "ownership", "sort_order"),
    )

    def __repr__(self) -> str:  # pragma: no cover - 僅除錯用
        return f"<Equipment {self.slug!r} {self.ownership} {self.publish_status}>"

    @property
    def category_label_zh(self) -> str:
        return EquipmentCategory.LABELS_ZH.get(self.category, self.category)

    @property
    def ownership_label_zh(self) -> str:
        return EquipmentOwnership.LABELS_ZH.get(self.ownership, self.ownership)
