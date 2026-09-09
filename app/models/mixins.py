# ============================================================
# NTUST SiPh Lab - Model Mixins & Domain Enumerations
#
# 上下游：
#   extensions.db (Base)
#       -> models/mixins.py (TimestampMixin / 狀態常數)
#       -> models/person.py, models/research_output.py, ...
#       -> repositories/*.py 以常數過濾查詢
#       -> blueprints/admin/forms.py 以常數產生 select 選項
#
# 檔案路徑：
#   app/models/mixins.py
#
# 建立日期：2026-08-14
# 最後重大修改：2026-08-14
# 版本：v1.0
#
# 模組定位與責任邊界：
#   集中定義「跨 model 共用的欄位行為」與「領域狀態字面值」。
#   SAI §8.9 要求所有 datetime 以 UTC 儲存、前台再轉 Asia/Taipei；
#   SAI §7.4 定義三種 publish status；§8.3 定義三種 person status；
#   §8.4 定義七種 output type。這些字串若散落在 route/template，
#   拼錯不會報錯只會查不到資料，因此統一收在此檔。
#
#   責任邊界（不得做的事）：
#     - 不得在此定義具體 table。
#     - 不得 import 其他 model（保持最底層）。
#
# 輸入 -> 處理 -> 輸出 Pipeline：
#   輸入：ORM 事件（insert / update）
#   處理：utcnow() 產生 timezone-aware UTC 時間
#   輸出：created_at / updated_at 欄位值
#
# 主要 Class / Function：
#   utcnow()          - 產生 timezone-aware UTC 現在時間
#   TimestampMixin    - created_at / updated_at 欄位與自動更新
#   PublishStatus     - draft / published / archived
#   PersonStatus      - faculty / current / alumni
#   OutputType        - journal / conference / project / prototype /
#                       simulation / dataset / other
#   EquipmentOwnership- lab / institute / shared（歸屬層級）
#   EquipmentCategory - measurement / packaging / inspection / source /
#                       computing / component / other
#   ContributorRole   - author / student / supervisor / contributor
#   AuditAction       - AuditLog.action 允許值
#   MigrationStatus   - legacy 遷移狀態（SAI §22.1）
#
# 依賴套件：
#   sqlalchemy, 標準庫 datetime
#
# 環境變數：
#   無。
#
# 資料庫使用方式：
#   DateTime(timezone=True)。SQLite 不真正保存 tzinfo，
#   但 SQLAlchemy 會在讀取時附回 UTC；PostgreSQL 使用 timestamptz。
#   兩者在應用層行為一致，這是可攜契約的一部分。
#
# Error Handling / Fallback：
#   本檔為純資料定義，無 I/O，不含錯誤處理路徑。
#
# 特殊機制：
#   updated_at 使用 onupdate=utcnow，由 SQLAlchemy 在 flush 時填值。
#   注意：這只在「透過 ORM 修改」時觸發；若未來加入 bulk update
#   （query.update()），必須手動帶入 updated_at，否則 sitemap 的
#   lastmod（SAI §12.1）會停止更新。
#
# 已知限制與禁止事項：
#   1. 禁止使用 datetime.utcnow()（naive），會造成時區比較錯誤。
#      一律使用本檔 utcnow()。
#   2. 禁止在 DB 層使用 native ENUM 型別 —— PostgreSQL 的 ENUM
#      需要額外 migration 才能新增值，SQLite 則無此型別。
#      改用 VARCHAR + CheckConstraint（SAI §10.2 要求 constraint
#      必須在 migration 明確定義，且兩種 DB 行為一致）。
#
# 維護契約：
#   新增狀態值時必須同時：
#     (a) 更新此處常數與 ALL 元組
#     (b) 建立 Alembic migration 更新 CheckConstraint
#     (c) 更新 admin form 的 choices
#   只改其中一處會造成「後台可選但 DB 拒絕」或反之。
#
# 驗證方式：
#   pytest tests/test_schema.py
# ============================================================

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, TypeDecorator
from sqlalchemy.orm import Mapped, mapped_column


class UtcDateTime(TypeDecorator):
    """永遠以 UTC-aware datetime 進出的時間欄位型別。

    為什麼需要這個型別（SAI §10.2 可攜契約、§24 風險對策）：
      SQLite 沒有原生的時間型別，也不保存時區資訊。即使欄位宣告為
      DateTime(timezone=True)，SQLite 讀回來的仍是 **naive** datetime；
      PostgreSQL 的 timestamptz 則會回傳 **aware** datetime。

      這個差異非常危險：任何「把資料庫時間與 utcnow() 相比」的程式碼
      在 PostgreSQL 正常、在 SQLite 會拋 TypeError（或反之）。
      問題只會在切換資料庫時才浮現，正是 SAI §24 列為高風險的
      「SQLite -> PostgreSQL 行為差異」。

      本型別在讀寫兩端都強制轉換，讓兩種資料庫的行為完全一致：
        寫入：naive 視為 UTC 補上 tzinfo -> 轉為 UTC 後存入
        讀取：naive 補上 UTC tzinfo -> 一律回傳 aware UTC

    修改會影響什麼：
      移除或改回原生 DateTime 會讓 tests/test_schema.py 的
      時區測試失敗，並在部署到 PostgreSQL 時產生難以重現的
      時間比較錯誤。

    實作註記：
      impl 仍是 DateTime(timezone=True)，因此底層 DDL 與
      原本完全相同，不需要額外的 migration。
      cache_ok=True 讓 SQLAlchemy 可以快取使用此型別的查詢編譯結果。
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        """寫入資料庫前：一律轉為 UTC-aware。"""
        if value is None:
            return None
        if value.tzinfo is None:
            # 專案所有寫入路徑都使用 utcnow()，因此 naive 值
            # 依慣例視為 UTC（例如外部工具直接寫入的資料）。
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):
        """從資料庫讀出後：一律回傳 UTC-aware。"""
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


def utcnow() -> datetime:
    """回傳 timezone-aware 的 UTC 現在時間。

    為什麼不用 datetime.utcnow()：
      datetime.utcnow() 回傳 naive datetime（無 tzinfo），與從
      PostgreSQL timestamptz 讀回的 aware datetime 相比會直接
      TypeError。統一使用 aware 值可讓同一段比較邏輯在 SQLite 與
      PostgreSQL 都成立（SAI §10.2）。
    """
    return datetime.now(timezone.utc)


class TimestampMixin:
    """為 model 提供 created_at / updated_at。

    設計理由：
      SAI §12.1 要求 sitemap 以 updated_at 當 lastmod、
      §13.1 要求每頁顯示 last updated。把時間戳做成 mixin 可確保
      每張內容表都具備這兩個欄位，不會漏掉其中一張。

    修改會影響什麼：
      移除 updated_at 會同時破壞 sitemap lastmod、前台
      "最後更新" 顯示與 Admin "Recent changes" 排序。
    """

    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime,
        nullable=False,
        default=utcnow,
    )
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime,
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
    )


class PublishStatus:
    """內容發布狀態（SAI §7.4）。"""

    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"
    ALL = (DRAFT, PUBLISHED, ARCHIVED)
    #: 可出現在前台與 sitemap 的狀態集合。
    PUBLIC = (PUBLISHED,)


class PersonStatus:
    """人物狀態（SAI §8.3）。

    注意 ADR-008：在學生畢業「只改狀態」，不建立第二個 Person，
    因此 current -> alumni 是同一 row 的欄位變更。
    """

    FACULTY = "faculty"
    CURRENT = "current"
    ALUMNI = "alumni"
    ALL = (FACULTY, CURRENT, ALUMNI)


class OutputType:
    """研究成果型別（SAI §8.4）。

    ADR-009：期刊、會議、專案、模擬、系統等以 type 分流，
    不複製資料模型。schema_service 依此決定輸出
    ScholarlyArticle 或 CreativeWork（SAI §12.2）。
    """

    JOURNAL = "journal"
    CONFERENCE = "conference"
    PROJECT = "project"
    PROTOTYPE = "prototype"
    SIMULATION = "simulation"
    DATASET = "dataset"
    OTHER = "other"
    ALL = (JOURNAL, CONFERENCE, PROJECT, PROTOTYPE, SIMULATION, DATASET, OTHER)

    #: 只有真正的學術論文才可輸出 ScholarlyArticle（SAI §12.2 [S14]）。
    SCHOLARLY = (JOURNAL, CONFERENCE)

    #: 顯示用中文標籤。放在 model 層是為了讓 Admin 表單、公開頁 badge
    #: 與 sitemap 描述共用同一份文字，避免三處各自翻譯造成不一致
    #: （SAI §13.1 Consistent naming）。
    LABELS_ZH = {
        JOURNAL: "期刊論文",
        CONFERENCE: "會議論文",
        PROJECT: "研究專案",
        PROTOTYPE: "原型系統",
        SIMULATION: "模擬研究",
        DATASET: "資料集",
        OTHER: "其他成果",
    }
    LABELS_EN = {
        JOURNAL: "Journal Article",
        CONFERENCE: "Conference Paper",
        PROJECT: "Research Project",
        PROTOTYPE: "Prototype System",
        SIMULATION: "Simulation Study",
        DATASET: "Dataset",
        OTHER: "Other Output",
    }


class EquipmentOwnership:
    """設備的歸屬層級。

    為什麼這個欄位必須存在：
      實驗室頁面上的設備有三種來源，混在同一張清單會產生實質的
      不實陳述。「矽光子自動化封裝設備」屬於華夏校區半導體創新與
      應用研究中心，全台團隊都能預約；把它列為「本實驗室設備」，
      等於對想報考的學生宣稱實驗室擁有一台它沒有的機台。

      分成三層之後，同一筆資料可以誠實呈現：不是「我們有這台」，
      而是「在這裡你能用到這台」—— 對招生的說服力相同，但不需要
      說謊。

    LAB 一律需要人工確認才會存在：網路上查不到實驗室自有設備的
    任何公開資料（舊站、學院、研發中心、電子系實驗室列表都沒有），
    因此這一層只能由教授提供，不得從論文或同類實驗室推測。
    """

    LAB = "lab"
    INSTITUTE = "institute"
    SHARED = "shared"
    ALL = (LAB, INSTITUTE, SHARED)

    LABELS_ZH = {
        LAB: "本實驗室設備",
        INSTITUTE: "所屬中心共用設施",
        SHARED: "可申請使用的平台",
    }
    LABELS_EN = {
        LAB: "Lab Equipment",
        INSTITUTE: "Shared Facilities at the Institute",
        SHARED: "External Platforms Available on Request",
    }
    #: 前台分組顯示順序：由「最貼近實驗室」到「最外圍」。
    DISPLAY_ORDER = (LAB, INSTITUTE, SHARED)


class EquipmentCategory:
    """設備類別（用於前台標籤與後台篩選）。"""

    MEASUREMENT = "measurement"
    PACKAGING = "packaging"
    INSPECTION = "inspection"
    SOURCE = "source"
    COMPUTING = "computing"
    COMPONENT = "component"
    OTHER = "other"
    ALL = (
        MEASUREMENT, PACKAGING, INSPECTION, SOURCE,
        COMPUTING, COMPONENT, OTHER,
    )

    LABELS_ZH = {
        MEASUREMENT: "量測",
        PACKAGING: "封裝",
        INSPECTION: "檢測",
        SOURCE: "光源與訊號源",
        COMPUTING: "運算硬體",
        COMPONENT: "光學元件",
        OTHER: "其他",
    }
    LABELS_EN = {
        MEASUREMENT: "Measurement",
        PACKAGING: "Packaging",
        INSPECTION: "Inspection",
        SOURCE: "Light & Signal Sources",
        COMPUTING: "Computing Hardware",
        COMPONENT: "Optical Components",
        OTHER: "Other",
    }


class ContributorRole:
    """成果與人物的關聯角色（SAI §8.5）。"""

    AUTHOR = "author"
    STUDENT = "student"
    SUPERVISOR = "supervisor"
    CONTRIBUTOR = "contributor"
    ALL = (AUTHOR, STUDENT, SUPERVISOR, CONTRIBUTOR)
    LABELS_ZH = {
        AUTHOR: "作者",
        STUDENT: "學生研究者",
        SUPERVISOR: "指導教授",
        CONTRIBUTOR: "貢獻者",
    }


class AuditAction:
    """AuditLog.action 允許值（SAI §8.8）。"""

    CREATE = "create"
    UPDATE = "update"
    PUBLISH = "publish"
    ARCHIVE = "archive"
    LOGIN = "login"
    LOGIN_FAILED = "login_failed"
    LOGOUT = "logout"
    PASSWORD_CHANGE = "password_change"
    GRADUATE = "graduate"
    DELETE = "delete"
    ALL = (
        CREATE, UPDATE, PUBLISH, ARCHIVE, LOGIN, LOGIN_FAILED,
        LOGOUT, PASSWORD_CHANGE, GRADUATE, DELETE,
    )


class MigrationStatus:
    """母站 legacy 內容遷移狀態（SAI §22.1）。

    為什麼放在程式碼而非只存在 CSV：
      scripts/verify_migration.py 需要以程式檢查
      「UNRESOLVED = 0」這條 launch gate（AC-22）。若狀態字面值只
      存在文件裡，驗證腳本會因拼字差異而誤判通過。
    """

    DISCOVERED = "DISCOVERED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    MIGRATED = "MIGRATED"
    APPROVED_REWRITE = "APPROVED_REWRITE"
    APPROVED_REMOVE = "APPROVED_REMOVE"
    UNRESOLVED = "UNRESOLVED"
    ALL = (
        DISCOVERED, REVIEW_REQUIRED, MIGRATED,
        APPROVED_REWRITE, APPROVED_REMOVE, UNRESOLVED,
    )
    #: 允許 launch 的狀態（SAI §22.1 表格「是否允許 Launch」欄）。
    LAUNCH_ALLOWED = (MIGRATED, APPROVED_REWRITE, APPROVED_REMOVE)
    #: 阻擋 launch 的狀態。
    LAUNCH_BLOCKING = (DISCOVERED, REVIEW_REQUIRED, UNRESOLVED)
