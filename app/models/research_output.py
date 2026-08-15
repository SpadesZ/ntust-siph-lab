# ============================================================
# NTUST SiPh Lab - ResearchOutput & ResearchOutputPerson Models
#
# 上下游：
#   Admin Route -> forms -> ResearchService -> ResearchOutput -> DB
#   Public Route -> repositories/research.py -> ResearchOutput -> Template
#   ResearchOutput <-> ResearchOutputPerson <-> Person（雙向導航 AC-07）
#   ResearchOutput -> SchemaService（ScholarlyArticle / CreativeWork）
#   ResearchOutput -> SEOService（title/description fallback）
#   ResearchOutput -> sitemap.xml（僅 published）
#
# 檔案路徑：
#   app/models/research_output.py
#
# 建立日期：2026-08-14
# 最後重大修改：2026-08-14
# 版本：v1.0
#
# 模組定位與責任邊界：
#   ADR-009「ResearchOutput 統一成果實體」。期刊、會議、專案、
#   原型、模擬、資料集皆為同一張表，以 output_type 分流，
#   不複製資料模型。SAI §5.3 定義的 Problem / Method / Results /
#   Significance 是 SEO/GEO 最重要的內容單位，故各自獨立欄位，
#   而非塞進單一 rich text。
#
#   責任邊界（不得做的事）：
#     - 不得在此決定 JSON-LD 型別（那是 services/schema_service.py，
#       因為需要同時考慮頁面可見內容一致性 [S7]）。
#     - 不得在此處理 slug 變更的 Redirect（那是 ResearchService）。
#     - 不得在此存取 storage backend。
#
# 輸入 -> 處理 -> 輸出 Pipeline：
#   輸入：Admin 表單 / seed script
#   處理：CheckConstraint 驗證型別與狀態、keywords JSON 序列化、
#         DOI 正規化（由 utils/validators.py 提供，service 呼叫）
#   輸出：ORM 物件供 template / SEO / schema 使用
#
# 主要 Class / Property：
#   ResearchOutput                 - 成果實體
#   ResearchOutput.keywords        - keywords_json 的 list 介面
#   ResearchOutput.is_scholarly    - 是否為真正的學術論文
#   ResearchOutput.lab_people      - 依 sort_order 排序的 Lab 參與者
#   ResearchOutput.doi_url         - DOI 轉可點擊 URL
#   ResearchOutputPerson           - 成果與人物的關聯（含角色與順序）
#
# 依賴套件：
#   sqlalchemy, app.extensions.db, app.models.mixins
#
# 環境變數：
#   無。hero_image_path 僅存 object key（SAI §10.4）。
#
# 資料庫使用方式：
#   research_outputs：UNIQUE(slug)；
#     index(publish_status, year, output_type)、index(is_featured)（SAI §8.9）。
#   research_output_people：UNIQUE(research_output_id, person_id)
#     避免重複關聯（SAI §8.9）；FK 皆 ON DELETE CASCADE。
#
# Error Handling / Fallback：
#   - keywords getter 對損毀 JSON 回 []，理由同 Person.skills。
#   - doi_url 對空值或格式異常回 None，template 以 if 判斷不顯示連結，
#     絕不輸出 https://doi.org/None 這種壞連結（SAI §12.1 404/301）。
#
# 特殊機制（Transaction）：
#   本 model 不 commit。ResearchService 在同一 transaction 內完成
#   「更新成果 + 建立 Redirect + 寫 AuditLog」（SAI §9.3）。
#
# 已知限制與禁止事項：
#   1. 禁止虛構 DOI、citation、publication status（SAI §14.3 publish gate、
#      skills/siph-lab-seo-geo/SKILL.md）。欄位留空優於填假值。
#   2. 外部共同作者不一定建立 Person；正式作者列請填
#      authors_display_text，research_output_people 只負責 Lab 內部
#      人物的可導航關聯（SAI §8.5）。
#   3. archived 成果預設不出現在前台與 sitemap。
#
# 維護契約：
#   1. 新增 output_type 時必須同時更新 mixins.OutputType.ALL、
#      LABELS_ZH/EN、Alembic CheckConstraint 與 schema_service 的
#      型別對照，四處缺一就會出現「可選但存不進去」或
#      「存得進去但 JSON-LD 型別錯誤」。
#   2. 修改 Problem/Method/Results/Significance 欄位結構時，必須確認
#      公開頁仍能回答 SAI §5.3 的四個問題，否則違反 GEO contract。
#
# 驗證方式：
#   pytest tests/test_research.py
#   pytest tests/test_schema.py
# ============================================================

from __future__ import annotations

import json
import re
from datetime import date

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    select,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.mixins import ContributorRole, OutputType, PublishStatus, TimestampMixin

#: 用來從各種 DOI 輸入形式中抽出裸 DOI 的樣式。
#: 接受 "10.1109/xxx"、"doi:10.1109/xxx"、"https://doi.org/10.1109/xxx"。
_DOI_PATTERN = re.compile(r"(10\.\d{4,9}/[-._;()/:a-z0-9]+)", re.IGNORECASE)


class ResearchOutput(TimestampMixin, db.Model):
    """研究成果（ADR-009 統一實體）。"""

    __tablename__ = "research_outputs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    #: 公開 URL 識別（/research/<slug>）。發布後視為永久（SAI §4.2）。
    slug: Mapped[str] = mapped_column(String(160), nullable=False, unique=True, index=True)

    output_type: Mapped[str] = mapped_column(String(30), nullable=False, default=OutputType.OTHER)

    #: 4 位數年份，列表分組與排序的主鍵依據。
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    #: 有正式出版日期時使用；沒有則只用 year（SAI §8.4）。
    publication_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # --- 標題（至少一語言必填，由 publish validator 檢查）---
    title_zh: Mapped[str | None] = mapped_column(Text, nullable=True)
    title_en: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- SAI §5.3 的四段式研究內容 ---
    summary_zh: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_en: Mapped[str | None] = mapped_column(Text, nullable=True)
    problem_zh: Mapped[str | None] = mapped_column(Text, nullable=True)
    problem_en: Mapped[str | None] = mapped_column(Text, nullable=True)
    method_zh: Mapped[str | None] = mapped_column(Text, nullable=True)
    method_en: Mapped[str | None] = mapped_column(Text, nullable=True)
    results_zh: Mapped[str | None] = mapped_column(Text, nullable=True)
    results_en: Mapped[str | None] = mapped_column(Text, nullable=True)
    significance_zh: Mapped[str | None] = mapped_column(Text, nullable=True)
    significance_en: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- 出版資訊 ---
    venue: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: 正規化後的裸 DOI（不含 https://doi.org/ 前綴）。
    #: 存裸值的理由：便於比對重複、且顯示端可自由決定前綴形式。
    doi: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    external_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    github_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    dataset_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    #: 正式作者列全文（含非 Lab 共同作者），SAI §8.5。
    authors_display_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: JSON 陣列字串（中英研究關鍵字）。
    keywords_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- 媒體 ---
    hero_image_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    hero_image_alt_zh: Mapped[str | None] = mapped_column(String(220), nullable=True)
    hero_image_alt_en: Mapped[str | None] = mapped_column(String(220), nullable=True)

    # --- 排序與狀態 ---
    is_featured: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    publish_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=PublishStatus.DRAFT
    )

    # --- SEO override ---
    seo_title_zh: Mapped[str | None] = mapped_column(String(180), nullable=True)
    seo_description_zh: Mapped[str | None] = mapped_column(String(320), nullable=True)

    # --- 關聯 ---
    person_links: Mapped[list["ResearchOutputPerson"]] = relationship(
        "ResearchOutputPerson",
        back_populates="research_output",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ResearchOutputPerson.sort_order",
    )

    __table_args__ = (
        CheckConstraint(
            "output_type IN ('journal', 'conference', 'project', 'prototype', "
            "'simulation', 'dataset', 'other')",
            name="ck_research_outputs_type",
        ),
        CheckConstraint(
            "publish_status IN ('draft', 'published', 'archived')",
            name="ck_research_outputs_publish_status",
        ),
        CheckConstraint("year >= 1900 AND year <= 2200", name="ck_research_outputs_year_range"),
        # SAI §8.9 指定索引。
        Index("ix_research_publish_year_type", "publish_status", "year", "output_type"),
        Index("ix_research_featured", "is_featured"),
    )

    def __repr__(self) -> str:  # pragma: no cover - 僅除錯用
        return f"<ResearchOutput {self.slug!r} {self.output_type} {self.year}>"

    # ------------------------------------------------------------------
    # keywords JSON 介面
    # ------------------------------------------------------------------
    @property
    def keywords(self) -> list[str]:
        """關鍵字 list。損毀 JSON 回 []（理由同 Person.skills）。"""
        if not self.keywords_json:
            return []
        try:
            value = json.loads(self.keywords_json)
        except (ValueError, TypeError):
            return []
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    @keywords.setter
    def keywords(self, values) -> None:
        """設定關鍵字，trim + 去重 + 保留順序（SAI §15.2「去重/trim」）。"""
        if not values:
            self.keywords_json = json.dumps([], ensure_ascii=False)
            return
        seen: set[str] = set()
        cleaned: list[str] = []
        for item in values:
            text = str(item).strip()
            key = text.lower()
            if text and key not in seen:
                seen.add(key)
                cleaned.append(text)
        self.keywords_json = json.dumps(cleaned, ensure_ascii=False)

    # ------------------------------------------------------------------
    # 顯示輔助
    # ------------------------------------------------------------------
    @staticmethod
    def normalize_doi(raw: str | None) -> str | None:
        """把各種 DOI 輸入形式正規化為裸 DOI。

        為什麼要正規化：
          SAI §15.2 要求「DOI 格式 normalization」。管理者可能貼上
          完整 URL、doi: 前綴或裸值；若原樣儲存，同一篇論文會產生
          三種值，重複偵測與 JSON-LD identifier 都會失準。

        回傳 None 表示輸入不含合法 DOI —— 呼叫端應把它當成
        「使用者輸入錯誤」而非「沒有 DOI」，由表單驗證回報。
        """
        if not raw:
            return None
        match = _DOI_PATTERN.search(raw.strip())
        if not match:
            return None
        return match.group(1).rstrip(".").lower()

    @property
    def doi_url(self) -> str | None:
        """DOI 的可點擊 URL；無 DOI 回 None。"""
        return f"https://doi.org/{self.doi}" if self.doi else None

    @property
    def is_scholarly(self) -> bool:
        """是否為真正的學術論文（決定可否輸出 ScholarlyArticle）。

        SAI §12.2 [S14]：只有實際 scholarly article 才使用該型別；
        一般研究展示必須保守地使用 CreativeWork。
        """
        return self.output_type in OutputType.SCHOLARLY

    @property
    def type_label_zh(self) -> str:
        """型別中文標籤（供 badge 顯示）。"""
        return OutputType.LABELS_ZH.get(self.output_type, OutputType.LABELS_ZH[OutputType.OTHER])

    @property
    def type_label_en(self) -> str:
        return OutputType.LABELS_EN.get(self.output_type, OutputType.LABELS_EN[OutputType.OTHER])

    @property
    def display_title(self) -> str:
        """優先中文標題，缺則英文，皆缺則以 slug 兜底。

        為什麼要兜底：
          draft 階段允許標題未填，Admin 列表仍需要可辨識的字串。
          前台不會遇到此情況，因為 publish validator 會擋下無標題成果。
        """
        return self.title_zh or self.title_en or self.slug

    @property
    def lab_people(self) -> list["Person"]:
        """依 sort_order 排序的 Lab 參與人物（含未發布者，供 Admin 用）。"""
        return [link.person for link in self.person_links if link.person is not None]

    @property
    def public_lab_people(self) -> list["Person"]:
        """只含可公開人物，供前台雙向導航使用（AC-07）。"""
        return [p for p in self.lab_people if p.is_publicly_visible]

    @property
    def has_research_body(self) -> bool:
        """是否具備足以回答 SAI §5.3 的研究內容。

        用於 publish validator 與 Admin "Needs attention"。
        設計理由：論文/代表成果建議至少 Method + Results（SAI §15.2）。
        """
        return bool(self.method_zh or self.method_en) and bool(self.results_zh or self.results_en)

    # ------------------------------------------------------------------
    # 查詢輔助
    # ------------------------------------------------------------------
    @classmethod
    def find_by_slug(cls, slug: str) -> "ResearchOutput | None":
        if not slug:
            return None
        return db.session.scalar(select(cls).where(cls.slug == slug))


class ResearchOutputPerson(db.Model):
    """成果 <-> 人物關聯（SAI §8.5）。

    為什麼用顯式 association object 而非單純 secondary table：
      需要額外欄位 contributor_role 與 sort_order（作者順序）。
      作者順序在學術情境是有意義的資訊，不能靠 insert 順序碰運氣。
    """

    __tablename__ = "research_output_people"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    research_output_id: Mapped[int] = mapped_column(
        ForeignKey("research_outputs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    person_id: Mapped[int] = mapped_column(
        ForeignKey("people.id", ondelete="CASCADE"), nullable=False, index=True
    )

    contributor_role: Mapped[str | None] = mapped_column(
        String(40), nullable=True, default=ContributorRole.AUTHOR
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=100)

    research_output: Mapped["ResearchOutput"] = relationship(
        "ResearchOutput", back_populates="person_links"
    )
    person: Mapped["Person"] = relationship("Person", back_populates="output_links")

    __table_args__ = (
        # SAI §8.9：避免同一人被重複掛在同一成果上。
        UniqueConstraint("research_output_id", "person_id", name="uq_output_person"),
        CheckConstraint(
            "contributor_role IS NULL OR contributor_role IN "
            "('author', 'student', 'supervisor', 'contributor')",
            name="ck_output_person_role",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - 僅除錯用
        return f"<ResearchOutputPerson output={self.research_output_id} person={self.person_id}>"

    @property
    def role_label_zh(self) -> str:
        """角色中文標籤；未設定時視為作者。"""
        return ContributorRole.LABELS_ZH.get(
            self.contributor_role or ContributorRole.AUTHOR,
            ContributorRole.LABELS_ZH[ContributorRole.AUTHOR],
        )


# 檔案末端 import，理由同 person.py（雙向 relationship 的循環相依）。
from app.models.person import Person  # noqa: E402,F401
