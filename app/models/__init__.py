# ============================================================
# NTUST SiPh Lab - Models Package Aggregator
#
# 上下游：
#   app/__init__.py (create_app) -> import app.models
#       -> 觸發所有 model 模組載入 -> SQLAlchemy metadata 完整
#       -> migrations/env.py 得以 autogenerate 正確的 schema
#
# 檔案路徑：
#   app/models/__init__.py
#
# 建立日期：2026-08-14
# 最後重大修改：2026-08-14
# 版本：v1.0
#
# 模組定位與責任邊界：
#   單純的匯總層。存在的唯一理由是「保證所有 model 都被 import」——
#   Alembic autogenerate 只看得到已載入的 model，若某張表沒被
#   import，migration 會靜默漏掉它，直到 production 查詢失敗才發現。
#
#   責任邊界：不得在此定義任何 model、邏輯或查詢。
#
# 輸入 -> 處理 -> 輸出 Pipeline：
#   輸入：無（import side effect）
#   處理：依相依順序 import 各 model 模組
#   輸出：__all__ 匯出名稱、完整的 db.metadata
#
# 主要匯出：
#   AdminUser, Person, ResearchOutput, ResearchOutputPerson,
#   SiteSetting, Redirect, AuditLog
#   以及 mixins 的狀態常數
#
# 依賴套件：
#   app.extensions.db（間接）
#
# 環境變數：無。
# 資料庫使用方式：無直接存取。
#
# Error Handling / Fallback：
#   無。任何 import 錯誤都應該讓 app 啟動失敗（fail-fast），
#   因為 model 載入不完整會造成難以診斷的 schema 漂移。
#
# 特殊機制：
#   import 順序刻意為 admin_user -> person -> research_output，
#   因為 person 與 research_output 互相參照，且兩者的檔案末端
#   都有補完 import。先載入 person 可確保 relationship 字串
#   在 mapper 設定時能解析成功。
#
# 已知限制與禁止事項：
#   禁止為了「避免 unused import 警告」而刪除任何 import；
#   那會讓對應的資料表從 Alembic metadata 中消失。
#   故統一以 __all__ 宣告並標註 noqa: F401。
#
# 維護契約：
#   新增 model 檔案時，必須在此加入 import 與 __all__，
#   否則 flask db migrate 不會產生該表的 migration。
#
# 驗證方式：
#   pytest tests/test_schema.py::test_all_tables_present
#   flask db migrate --autogenerate（應產生空 diff）
# ============================================================

from __future__ import annotations

from app.models.mixins import (  # noqa: F401
    AuditAction,
    ContributorRole,
    MigrationStatus,
    OutputType,
    PersonStatus,
    PublishStatus,
    TimestampMixin,
    utcnow,
)
from app.models.admin_user import AdminUser  # noqa: F401
from app.models.person import Person  # noqa: F401
from app.models.research_output import ResearchOutput, ResearchOutputPerson  # noqa: F401
from app.models.site_setting import SiteSetting  # noqa: F401
from app.models.redirect import Redirect  # noqa: F401
from app.models.audit_log import AuditLog  # noqa: F401

#: SAI §8 定義的 7 類核心 table 對應的 model 類別。
#: tests/test_schema.py 以此清單驗證資料表齊全。
__all__ = [
    "AdminUser",
    "Person",
    "ResearchOutput",
    "ResearchOutputPerson",
    "SiteSetting",
    "Redirect",
    "AuditLog",
    # 常數
    "AuditAction",
    "ContributorRole",
    "MigrationStatus",
    "OutputType",
    "PersonStatus",
    "PublishStatus",
    "TimestampMixin",
    "utcnow",
]
