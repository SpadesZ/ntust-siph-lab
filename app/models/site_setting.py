# ============================================================
# NTUST SiPh Lab - SiteSetting Model (Singleton)
#
# 上下游：
#   Admin /admin/settings -> SiteSettingForm -> SiteSetting -> DB
#   create_app() -> context processor -> SiteSetting.get() -> 所有 Template
#   SiteSetting -> SEOService（default title suffix / description / OG image）
#   SiteSetting -> SchemaService（Organization / WebSite JSON-LD）
#   SiteSetting -> /robots.txt、/llms.txt、/sitemap.xml
#
# 檔案路徑：
#   app/models/site_setting.py
#
# 建立日期：2026-08-14
# 最後重大修改：2026-08-14
# 版本：v1.0
#
# 模組定位與責任邊界：
#   全站可由後台維護的文案與設定的唯一來源（SAI §8.6）。
#   採 singleton row（id=1）而非 key-value table —— SAI §8.6 明確
#   指出理由：欄位數有限且需要型別與表單驗證。key-value 會失去
#   欄位型別、無法用 WTForms 驗證，也讓 migration 無法表達約束。
#
#   責任邊界（不得做的事）：
#     - 不得存放 secret（SECRET_KEY、DB 密碼、API credential）。
#       SAI §8.9 明確禁止；那些一律走環境變數 / Secret Manager。
#     - 不得存放人物資料（教授資料在 Person，SAI §15.4 Professor tab
#       只選 featured professor）。
#
# 輸入 -> 處理 -> 輸出 Pipeline：
#   輸入：Admin Site Settings 表單（六個 tab）
#   處理：JSON 欄位序列化、get() 的 lazy 建立
#   輸出：全站 template context 的 `settings` 物件
#
# 主要 Class / Function：
#   SiteSetting.get()            - 取得（必要時建立）singleton row
#   SiteSetting.research_focus   - 首頁研究主題清單的 list 介面
#   SiteSetting.social_links     - sameAs 用外部連結清單
#   SiteSetting.canonical_base   - 去除尾斜線的 base URL
#
# 依賴套件：
#   sqlalchemy, app.extensions.db
#
# 環境變數：
#   PUBLIC_BASE_URL 為 canonical 的最終來源（config.py）。
#   本表的 production_base_url 欄位僅作「管理者可見的紀錄與提示」，
#   實際 canonical 一律以 app.config['PUBLIC_BASE_URL'] 為準 ——
#   理由見下方 canonical_base 的 docstring。
#
# 資料庫使用方式：
#   site_settings table，僅一列（id=1）。
#   get() 若找不到會建立預設列，讓全新資料庫也能直接啟動
#   （支撐 SAI §23 P0「本機 compose 可跑」）。
#
# Error Handling / Fallback：
#   - JSON 欄位解析失敗一律回 []，不讓髒資料造成首頁 500。
#   - get() 在 read-only 資料庫（例如某些 restore drill 情境）
#     無法建立時，會回傳未 persist 的暫時物件，讓頁面仍可 render。
#
# 特殊機制（Transaction）：
#   get() 在建立預設列時會自行 commit。這是刻意例外：
#   它必須在任何 request（含 GET）都能成功，否則全新環境的第一次
#   造訪會失敗。其餘欄位更新一律由 admin route 控制交易邊界。
#
# 已知限制與禁止事項：
#   1. 禁止把此表當通用 CMS 塞任意內容；新增欄位必須有對應表單。
#   2. 禁止在此存放任何 secret。
#   3. 禁止讓 llms_txt_enabled 的說明文字暗示它會影響排名
#      （SAI §13.2 / §15.4 明確要求標示為實驗性相容層）。
#
# 維護契約：
#   新增欄位時必須同時更新：
#     (a) blueprints/admin/forms.py 的 SiteSettingForm
#     (b) templates/admin/settings.html 的對應 tab
#     (c) Alembic migration
#   缺 (a)(b) 會造成「欄位存在但管理員無法維護」，違反使用者需求
#   「所有網站主要內容均須可由後台維護」。
#
# 驗證方式：
#   pytest tests/test_seo.py
#   pytest tests/test_schema.py
# ============================================================

from __future__ import annotations

import json

from sqlalchemy import Boolean, Integer, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db
from app.models.mixins import TimestampMixin

#: singleton row 的固定主鍵。
SINGLETON_ID = 1


class SiteSetting(TimestampMixin, db.Model):
    """全站設定（singleton，SAI §8.6）。"""

    __tablename__ = "site_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=SINGLETON_ID)

    # ------------------------------------------------------------------
    # Identity（母站 LC-001 Lab 名稱的落點）
    # ------------------------------------------------------------------
    lab_name_zh: Mapped[str] = mapped_column(String(160), nullable=False, default="NTUST SiPh Lab")
    lab_name_en: Mapped[str] = mapped_column(String(200), nullable=False, default="NTUST SiPh Lab")
    short_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    department_zh: Mapped[str | None] = mapped_column(String(160), nullable=True)
    department_en: Mapped[str | None] = mapped_column(String(200), nullable=True)
    university_zh: Mapped[str | None] = mapped_column(String(160), nullable=True)
    university_en: Mapped[str | None] = mapped_column(String(200), nullable=True)
    logo_path: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # ------------------------------------------------------------------
    # Homepage Hero（SAI §5.1：Hero 必須是 Lab 的論點，不是裝飾統計）
    # ------------------------------------------------------------------
    hero_title_zh: Mapped[str | None] = mapped_column(Text, nullable=True)
    hero_title_en: Mapped[str | None] = mapped_column(Text, nullable=True)
    hero_intro_zh: Mapped[str | None] = mapped_column(Text, nullable=True)
    hero_intro_en: Mapped[str | None] = mapped_column(Text, nullable=True)
    hero_media_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    hero_media_alt_zh: Mapped[str | None] = mapped_column(String(220), nullable=True)

    #: 首頁 Research focus section（SAI §5.1 順序 02）。
    #: JSON 陣列，元素為 {"title_zh", "title_en", "description_zh"}。
    #: 母站 LC-006~LC-011 六項專長遷入此處（§22.3「六項集合比對，不可漏項」）。
    #: description 允許為空：母站沒有定義文字，依 §2.3 不得由 Agent 猜測填入。
    research_focus_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: 首頁 Lab proof section（SAI §5.1 順序 05）。
    #: JSON 陣列 [{"label_zh", "value_zh", "source"}]。
    #: 只放可驗證事實，禁止 vanity metrics（SAI §6.3）。
    lab_proof_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ------------------------------------------------------------------
    # Contact（母站 LC-012 Email 的落點之一）
    # ------------------------------------------------------------------
    contact_email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    address_zh: Mapped[str | None] = mapped_column(Text, nullable=True)
    address_en: Mapped[str | None] = mapped_column(Text, nullable=True)
    map_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # ------------------------------------------------------------------
    # Join（招募）
    # ------------------------------------------------------------------
    join_title_zh: Mapped[str | None] = mapped_column(String(200), nullable=True)
    join_body_zh: Mapped[str | None] = mapped_column(Text, nullable=True)
    join_cta_label_zh: Mapped[str | None] = mapped_column(String(120), nullable=True)
    join_cta_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # ------------------------------------------------------------------
    # About（SAI §4.1 /about：教授、Lab、研究方向與設備/方法概覽）
    # ------------------------------------------------------------------
    about_intro_zh: Mapped[str | None] = mapped_column(Text, nullable=True)
    about_methods_zh: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ------------------------------------------------------------------
    # SEO defaults（SAI §8.6 / §12）
    # ------------------------------------------------------------------
    default_title_suffix: Mapped[str | None] = mapped_column(String(120), nullable=True)
    default_description_zh: Mapped[str | None] = mapped_column(String(320), nullable=True)
    og_image_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: 管理者可見的正式網域紀錄。實際 canonical 仍以環境變數為準。
    production_base_url: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # ------------------------------------------------------------------
    # External identity（sameAs / footer，母站 LC-013 外鏈落點之一）
    # ------------------------------------------------------------------
    official_ntust_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    #: JSON 陣列 [{"label", "url"}]，供 footer 與 Organization.sameAs 使用。
    social_links_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ------------------------------------------------------------------
    # Advanced（SAI §15.4）
    # ------------------------------------------------------------------
    #: 實驗性相容層開關（SAI §13.2）。UI 必須附說明，
    #: 不得讓管理員誤以為這是排名開關。
    llms_txt_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    def __repr__(self) -> str:  # pragma: no cover - 僅除錯用
        return f"<SiteSetting id={self.id} lab={self.lab_name_zh!r}>"

    # ------------------------------------------------------------------
    # Singleton 取得
    # ------------------------------------------------------------------
    @classmethod
    def get(cls) -> "SiteSetting":
        """取得 singleton；不存在時建立預設列。

        為什麼允許在 GET request 中寫入資料庫：
          全新資料庫（例如剛跑完 flask db upgrade 還沒 seed）若沒有
          設定列，首頁 context processor 會拿到 None，導致所有
          template 需要到處寫 `if settings`。與其讓 100 個 template
          防禦，不如保證這個物件永遠存在。

        Fallback：
          若資料庫不可寫（restore drill 或 read replica），
          回傳未 persist 的暫時物件，讓頁面仍能 render，
          並把錯誤留給 log 而非使用者。
        """
        setting = db.session.get(cls, SINGLETON_ID)
        if setting is not None:
            return setting

        setting = cls(id=SINGLETON_ID)
        try:
            db.session.add(setting)
            db.session.commit()
        except Exception:  # noqa: BLE001 - 任何寫入失敗都退回暫時物件
            db.session.rollback()
            # 從 session 中移除，避免後續 flush 再次嘗試寫入。
            db.session.expunge(setting)
        return setting

    # ------------------------------------------------------------------
    # JSON 欄位介面
    # ------------------------------------------------------------------
    @staticmethod
    def _load_json_list(raw: str | None) -> list:
        """共用的 JSON list 解析；失敗回 []。"""
        if not raw:
            return []
        try:
            value = json.loads(raw)
        except (ValueError, TypeError):
            return []
        return value if isinstance(value, list) else []

    @property
    def research_focus(self) -> list[dict]:
        """首頁研究主題清單。

        每個元素預期含 title_zh / title_en / description_zh。
        description_zh 可為空字串 —— 母站僅提供專長名稱，
        依 SAI §2.3 不得由 Agent 補寫定義，需由 Lab 補齊。
        """
        return [item for item in self._load_json_list(self.research_focus_json) if isinstance(item, dict)]

    @research_focus.setter
    def research_focus(self, values) -> None:
        self.research_focus_json = json.dumps(list(values or []), ensure_ascii=False)

    @property
    def lab_proof(self) -> list[dict]:
        """首頁可驗證事實清單（SAI §5.1 順序 05）。"""
        return [item for item in self._load_json_list(self.lab_proof_json) if isinstance(item, dict)]

    @lab_proof.setter
    def lab_proof(self, values) -> None:
        self.lab_proof_json = json.dumps(list(values or []), ensure_ascii=False)

    @property
    def social_links(self) -> list[dict]:
        """外部連結清單，供 footer 與 Organization.sameAs。"""
        return [
            item
            for item in self._load_json_list(self.social_links_json)
            if isinstance(item, dict) and item.get("url")
        ]

    @social_links.setter
    def social_links(self, values) -> None:
        self.social_links_json = json.dumps(list(values or []), ensure_ascii=False)

    # ------------------------------------------------------------------
    # 顯示輔助
    # ------------------------------------------------------------------
    @property
    def sameas_urls(self) -> list[str]:
        """Organization.sameAs 用的 URL 清單（SAI §12.2）。

        只輸出真實存在的連結。SAI §12.2 [S7] 要求 structured data
        不得描述使用者看不到的內容，因此這些 URL 也必須同時
        出現在 footer 或 About 頁。
        """
        urls: list[str] = []
        if self.official_ntust_url:
            urls.append(self.official_ntust_url)
        urls.extend(link["url"] for link in self.social_links if link.get("url"))
        # 去重且保留順序。
        seen: set[str] = set()
        return [u for u in urls if not (u in seen or seen.add(u))]

    @classmethod
    def exists(cls) -> bool:
        """singleton 是否已存在（供 seed script 判斷是否要初始化）。"""
        return db.session.scalar(select(cls.id).where(cls.id == SINGLETON_ID)) is not None
