# ============================================================
# NTUST SiPh Lab - Person Model
#
# 上下游：
#   Admin Route -> forms -> PersonService -> Person -> SQLAlchemy -> DB
#   Public Route -> repositories/people.py -> Person -> Jinja Template
#   Person <-> ResearchOutputPerson <-> ResearchOutput（雙向導航）
#   Person -> SEOService（title/description fallback）
#   Person -> SchemaService（Person JSON-LD）
#
# 檔案路徑：
#   app/models/person.py
#
# 建立日期：2026-08-14
# 最後重大修改：2026-08-14
# 版本：v1.0
#
# 模組定位與責任邊界：
#   ADR-008「Person 單一人物實體」的載體。教授、在學生、畢業生
#   全部是同一張表的 row，僅以 status 區分。在學生畢業時只改
#   status 與補畢業欄位，person id 與 slug 不變，因此既有
#   ResearchOutput 關聯不會斷裂（SAI §7.5 / AC-06）。
#
#   責任邊界（不得做的事）：
#     - 不得在此實作 graduate transition 的流程與稽核
#       （那是 services/person_service.py，因為需要寫 AuditLog）。
#     - 不得在此組 SEO 字串（那是 services/seo_service.py）。
#     - 不得在此存取 request context 或 storage backend。
#
# 輸入 -> 處理 -> 輸出 Pipeline：
#   輸入：Admin 表單欄位 / seed script
#   處理：欄位約束（CheckConstraint）、skills JSON 序列化、
#         display property 組合
#   輸出：ORM 物件供 template 與 service 使用
#
# 主要 Class / Property：
#   Person                     - 人物實體
#   Person.skills              - skills_json 的 list 介面
#   Person.display_name        - 中文名（英文名）組合
#   Person.is_publicly_visible - 是否應出現在前台
#   Person.related_outputs     - 已發布且可導航的成果（依 sort_order）
#
# 依賴套件：
#   sqlalchemy, app.extensions.db, app.models.mixins
#
# 環境變數：
#   無。photo_path 僅存 storage object key，實際 URL 由
#   MediaService/StorageBackend 產生（SAI §10.4：DB 不存絕對路徑）。
#
# 資料庫使用方式：
#   people table。UNIQUE(slug)；
#   composite index (status, publish_status, sort_order) 支援
#   /members 與 /alumni 列表查詢（SAI §8.9）。
#
# Error Handling / Fallback：
#   - skills setter 對 None 存入空陣列，getter 對損毀 JSON 回 []，
#     確保 template 迭代不會因為髒資料而 500。
#   - display_name 在缺英文名時只回中文名，不輸出空括號。
#
# 特殊機制（Transaction）：
#   本 model 不自行 commit。所有交易邊界由 service 層決定，
#   因為 slug 變更必須與 Redirect 建立、AuditLog 寫入在同一個
#   transaction 內完成（SAI §9.3），否則會出現「slug 已改但沒有
#   301」的不可逆狀態。
#
# 已知限制與禁止事項：
#   1. 禁止為了畢業而建立第二個 Person（ADR-008）。
#   2. 禁止在未確認可公開的情況下填 current_affiliation /
#      current_position（SAI §5.4 隱私規則、§24 資料隱私）。
#   3. 禁止把 email_public 用來存放不可公開的私人信箱。
#   4. slug 一經發布視為永久識別；變更必須產生 Redirect（SAI §4.2）。
#
# 維護契約：
#   1. 新增欄位時，若該欄位會出現在前台，必須同步評估
#      SchemaService 是否需要輸出 —— 但切記 SAI §12.2 [S7]：
#      structured data 不得包含頁面看不到的內容。
#   2. 修改 status / publish_status 允許值時，必須同時更新
#      models/mixins.py 常數與 Alembic CheckConstraint。
#   3. legacy_pending_detail 旗標不得被一般編輯流程自動清除；
#      必須由管理者補齊研究焦點後才由 PersonService 清除。
#
# 驗證方式：
#   pytest tests/test_people.py
#   pytest tests/test_schema.py
# ============================================================

from __future__ import annotations

import json

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Index,
    Integer,
    String,
    Text,
    select,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.mixins import PersonStatus, PublishStatus, TimestampMixin


class Person(TimestampMixin, db.Model):
    """研究室人物（教授 / 在學碩士生 / 畢業生）。

    為什麼三種身分共用一張表：
      ADR-008。研究成果的作者關聯必須在學生畢業後繼續有效。
      若畢業時把資料搬到 alumni 表，所有 foreign key 都要重寫，
      過程中任何失敗都會造成成果失去作者 —— 這是研究網站最不能
      接受的資料損壞。改狀態則是單欄位更新，無資料搬遷風險。
    """

    __tablename__ = "people"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    #: 公開 URL 識別（/people/<slug>）。發布後視為永久（SAI §4.2）。
    slug: Mapped[str] = mapped_column(String(120), nullable=False, unique=True, index=True)

    #: faculty / current / alumni（見 PersonStatus）。
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=PersonStatus.CURRENT)

    # --- 姓名 ---
    name_zh: Mapped[str] = mapped_column(String(120), nullable=False)
    name_en: Mapped[str | None] = mapped_column(String(160), nullable=True)

    # --- 學術身分 ---
    #: 例如 "副教授" / "M.S. Student"。SAI §8.3 degree 欄位。
    degree: Mapped[str | None] = mapped_column(String(80), nullable=True)
    #: 教授職稱或學生年級的顯示字串，對應母站 LC-004「副教授」。
    title_zh: Mapped[str | None] = mapped_column(String(120), nullable=True)
    title_en: Mapped[str | None] = mapped_column(String(160), nullable=True)
    #: 學歷（母站 LC-005「國立臺灣科技大學電子工程博士」）。
    education_zh: Mapped[str | None] = mapped_column(Text, nullable=True)
    education_en: Mapped[str | None] = mapped_column(Text, nullable=True)

    entry_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    graduation_year: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # --- 研究內容 ---
    #: SAI §8.3 標記 Y*：公開人物至少一個語言有值。
    #: 但四位母站碩二生僅有姓名（LC-015~018），依 2026-08-14 管理者裁示
    #: 以 legacy_pending_detail 旗標豁免，故此欄位 DB 層 nullable。
    research_focus_zh: Mapped[str | None] = mapped_column(Text, nullable=True)
    research_focus_en: Mapped[str | None] = mapped_column(Text, nullable=True)
    thesis_title_zh: Mapped[str | None] = mapped_column(Text, nullable=True)
    thesis_title_en: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: JSON 陣列字串。用 Text 而非 native JSON 型別以確保 SQLite 與
    #: PostgreSQL 行為完全一致（SAI §10.2 可攜契約）。
    skills_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    bio_zh: Mapped[str | None] = mapped_column(Text, nullable=True)
    bio_en: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- 畢業去向（隱私敏感，須確認可公開才填）---
    current_affiliation: Mapped[str | None] = mapped_column(String(200), nullable=True)
    current_position: Mapped[str | None] = mapped_column(String(160), nullable=True)
    #: 明確的公開同意旗標。SAI §15.3 要求 Admin 必須有
    #: 「不公開就業資訊」的明確選項；預設不公開才是安全預設。
    destination_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # --- 對外連結 ---
    email_public: Mapped[str | None] = mapped_column(String(200), nullable=True)
    orcid_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    scholar_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    github_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: 母站 LC-013 NTUST 官方頁外鏈。
    external_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    external_url_label: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # --- 媒體 ---
    #: StorageBackend 的 object key，例如 "people/uuid.jpg"。
    #: 絕不儲存 "/app/uploads/..." 這類容器絕對路徑（SAI §10.4）。
    photo_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    photo_alt_zh: Mapped[str | None] = mapped_column(String(200), nullable=True)
    photo_alt_en: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # --- 排序與狀態 ---
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    publish_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=PublishStatus.DRAFT
    )
    #: 首頁 professor block / featured 用。
    is_featured: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # --- SEO override（留空則由 SEOService fallback）---
    seo_title_zh: Mapped[str | None] = mapped_column(String(180), nullable=True)
    seo_description_zh: Mapped[str | None] = mapped_column(String(320), nullable=True)

    # --- Legacy 遷移旗標 ---
    #: True 表示此人物由母站遷入但欄位尚未由 Lab 補齊。
    #: 依 2026-08-16 管理者裁示，這類人物可在缺 research_focus 的情況下
    #: 發布（滿足 AC-23 四位成員必須可被找到），同時在 Admin Dashboard
    #: 的 "Needs attention" 持續提醒補齊。
    #: 完整決策記錄見 docs/adr/ADR-012-legacy-pending-publish-exemption.md
    #: （SAI 的 ADR 表僅到 ADR-011，本專案新增決策一律放 docs/adr/）。
    legacy_pending_detail: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: 對應 legacy/google_sites/migration_inventory.csv 的 legacy_id，
    #: 例如 "LC-015"。提供 verify_migration.py 做 old->new 對帳。
    legacy_id: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)

    # --- 關聯 ---
    #: 與成果的關聯物件。cascade delete-orphan 確保刪除 Person 時
    #: 不留下孤兒關聯 row；但實務上 SAI §7.6 預設不硬刪人物。
    output_links: Mapped[list["ResearchOutputPerson"]] = relationship(
        "ResearchOutputPerson",
        back_populates="person",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        # 以 CheckConstraint 取代 native ENUM：兩種 DB 行為一致，
        # 且新增值只需一次 migration（SAI §10.2）。
        CheckConstraint(
            "status IN ('faculty', 'current', 'alumni')",
            name="ck_people_status",
        ),
        CheckConstraint(
            "publish_status IN ('draft', 'published', 'archived')",
            name="ck_people_publish_status",
        ),
        # SAI §8.9：支援 /members 與 /alumni 列表的 composite index。
        Index("ix_people_status_publish_sort", "status", "publish_status", "sort_order"),
        Index("ix_people_graduation_year", "graduation_year"),
    )

    def __repr__(self) -> str:  # pragma: no cover - 僅除錯用
        return f"<Person {self.slug!r} status={self.status} publish={self.publish_status}>"

    # ------------------------------------------------------------------
    # skills_json 的 list 介面
    # ------------------------------------------------------------------
    @property
    def skills(self) -> list[str]:
        """把 skills_json 解析為 list。

        Fallback：解析失敗回空 list 而非 raise。
        為什麼：這個 property 直接被 template 迭代，若因為一筆髒資料
        就讓整個 /members 頁 500，代價遠高於少顯示幾個 tag。
        資料品質問題改由 Admin "Needs attention" 呈現。
        """
        if not self.skills_json:
            return []
        try:
            value = json.loads(self.skills_json)
        except (ValueError, TypeError):
            return []
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    @skills.setter
    def skills(self, values) -> None:
        """設定 skills，自動 trim、去重並保留原順序。"""
        if not values:
            self.skills_json = json.dumps([], ensure_ascii=False)
            return
        seen: set[str] = set()
        cleaned: list[str] = []
        for item in values:
            text = str(item).strip()
            if text and text not in seen:
                seen.add(text)
                cleaned.append(text)
        self.skills_json = json.dumps(cleaned, ensure_ascii=False)

    @property
    def distinctive_skills(self) -> list[str]:
        """只回傳「研究焦點文字裡沒講過」的 skills。

        為什麼需要這個（設計審查發現）：
          SAI §5.2 同時要求詳細頁有「研究焦點一句話」與
          「Methods & Tools」，前提是兩者內容不同。但實際資料中
          這兩個欄位常常是同一份清單 —— 例如教授的 research_focus_zh
          是「光電感測技術、矽光子技術、…」，skills 又是同樣六項。
          照樣渲染就會在同一個畫面把同一份資訊講兩次，
          違反本專案「資訊不要重複出現」的首要原則。

          在 model 這一層過濾而不是在 template 寫死條件，
          是因為任何列出 skills 的頁面都該套用同一條規則
          （person_detail、_person_card、未來的匯出）。

        行為：
          skills 中的字串若已完整出現在 research_focus_zh/en，
          視為重複而濾除。全部重複時回傳空 list，
          呼叫端據此隱藏整個區塊。
        """
        skills = self.skills
        if not skills:
            return []

        haystack = " ".join(
            filter(None, (self.research_focus_zh, self.research_focus_en))
        ).lower()
        if not haystack:
            return skills

        return [skill for skill in skills if skill.strip().lower() not in haystack]

    # ------------------------------------------------------------------
    # 顯示輔助
    # ------------------------------------------------------------------
    @property
    def display_name(self) -> str:
        """中文名（英文名）。缺英文名時不輸出空括號。"""
        if self.name_en:
            return f"{self.name_zh}（{self.name_en}）"
        return self.name_zh

    @property
    def is_publicly_visible(self) -> bool:
        """是否應出現在前台與 sitemap（SAI §7.4）。"""
        return self.publish_status == PublishStatus.PUBLISHED

    @property
    def public_destination(self) -> str | None:
        """已確認可公開的畢業去向字串；未確認則回 None。

        為什麼要在 model 這一層擋：
          SAI §5.4 與 §24 把「未經同意的就業資訊」列為隱私風險。
          若只靠 template 判斷，任何新增的頁面都可能忘記檢查。
          把規則收在 model 讓所有呼叫端預設安全。
        """
        if not self.destination_public:
            return None
        parts = [p for p in (self.current_affiliation, self.current_position) if p]
        return " / ".join(parts) if parts else None

    @property
    def related_outputs(self) -> list["ResearchOutput"]:
        """此人物參與且已發布的研究成果（新到舊）。

        為什麼在 model 提供而不是讓 template 查：
          SAI §9.1 明確禁止在 Jinja 做複雜 query。此 property 只做
          一次 in-memory 排序，資料本身由 relationship 載入。
        """
        outputs = [
            link.research_output
            for link in self.output_links
            if link.research_output is not None
            and link.research_output.publish_status == PublishStatus.PUBLISHED
        ]
        return sorted(
            outputs,
            key=lambda o: (-(o.year or 0), o.sort_order, o.id),
        )

    # ------------------------------------------------------------------
    # 查詢輔助
    # ------------------------------------------------------------------
    @classmethod
    def find_by_slug(cls, slug: str) -> "Person | None":
        """依 slug 查詢（不過濾發布狀態，供 Admin 與前台各自判斷）。"""
        if not slug:
            return None
        return db.session.scalar(select(cls).where(cls.slug == slug))


# 放在檔案末端 import 以避免 circular import：
# research_output.py 需要 person.py 的 Person，反之亦然。
# 這是 SQLAlchemy 雙向 relationship 的標準處理方式；
# 修改時請保持此 import 在檔案最下方。
from app.models.research_output import ResearchOutput, ResearchOutputPerson  # noqa: E402,F401
