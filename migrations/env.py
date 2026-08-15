# ============================================================
# NTUST SiPh Lab - Alembic Migration Environment
#
# 上下游：
#   flask db upgrade / downgrade / migrate
#       -> migrations/env.py（本檔）
#       -> app.extensions.db.metadata（由 app.models 匯總）
#       -> 目標資料庫（SQLite 或 PostgreSQL）
#
# 檔案路徑：
#   migrations/env.py
#
# 建立日期：2026-08-14
# 最後重大修改：2026-08-14
# 版本：v1.0
#
# 模組定位與責任邊界：
#   Alembic 的執行環境設定。SAI §10.2 的 Database Portability
#   Contract 要求「Alembic migration 必須在 SQLite test 與
#   PostgreSQL migration test 都能從 empty database 升到 head」，
#   本檔是那個保證的實作點。
#
#   責任邊界（不得做的事）：
#     - 不得在此定義 schema（那在 app/models/）。
#     - 不得在此寫入業務資料（seed 是 scripts/ 的責任）。
#
# 輸入 -> 處理 -> 輸出 Pipeline：
#   輸入：Flask app（由 flask db 指令建立）、db.metadata
#   處理：取得連線 URL、設定 batch mode、比較 metadata 與現況
#   輸出：升級/降級 DDL 或新的 migration 檔
#
# 主要 Function：
#   get_engine()      - 取得目前 app 的 SQLAlchemy engine
#   get_engine_url()  - 取得連線字串
#   get_metadata()    - 取得比對用的 metadata
#   run_migrations_offline / run_migrations_online
#
# 依賴套件：
#   alembic, flask, flask_sqlalchemy, flask_migrate
#
# 環境變數：
#   透過 app.config：SQLALCHEMY_DATABASE_URI
#
# 資料庫使用方式：
#   直接對目標資料庫執行 DDL。這是全專案唯一允許執行 DDL 的地方
#   —— 應用程式碼絕不呼叫 db.create_all()（見 app/__init__.py）。
#
# Error Handling / Fallback：
#   process_revision_directives 在偵測到「無 schema 變更」時
#   取消產生空的 migration 檔，避免 versions/ 累積無意義檔案。
#
# 特殊機制（render_as_batch）：
#   SQLite 不支援多數 ALTER TABLE 操作（變更欄位型別、加入具名
#   約束等）。batch mode 讓 Alembic 以「建新表 -> 複製資料 ->
#   刪舊表 -> 改名」完成變更。
#
#   本檔對「所有」dialect 都啟用 render_as_batch。
#   為什麼不只對 SQLite 啟用：
#     產生的 migration 檔必須「同一份」在兩種資料庫都能執行
#     （SAI §10.2）。若只在 SQLite 環境啟用，在 SQLite 上
#     autogenerate 出的 batch 語法，與在 PostgreSQL 上產生的
#     非 batch 語法會不同，導致同一個 migration 檔在另一種 DB
#     上行為不一致。統一使用 batch 可確保單一檔案通用；
#     PostgreSQL 執行 batch 區塊時會自動使用原生 ALTER，
#     對支援 ALTER 的後端而言 batch 是透明的。
#
# 特殊機制（compare_type）：
#   啟用型別與 server_default 比較。SAI §10.2 要求
#   「length、nullable 必須在 migration 中明確定義」，
#   關閉型別比較會讓這類變更被靜默忽略，
#   造成 SQLite 可用但 PostgreSQL 失敗。
#
# 已知限制與禁止事項：
#   1. 禁止手動編輯已套用到正式環境的 migration 檔；
#      必須新增一個新的 revision（SAI §21.6）。
#   2. 禁止在 migration 中 import app.models 的類別來操作資料 ——
#      model 會隨程式演進，舊 migration 會因此失效。
#      如需資料轉換，請在 migration 內以 sa.table()/sa.column()
#      定義輕量結構。
#   3. 禁止在 production 執行 `flask db migrate`（autogenerate）；
#      migration 檔必須在開發環境產生並經 review 後才部署。
#
# 維護契約：
#   任何 schema 變更都必須：
#     (a) 修改 app/models/
#     (b) 執行 flask db migrate 產生 revision
#     (c) 人工檢查 upgrade/downgrade 是否正確
#     (d) 在 SQLite 與 PostgreSQL 兩種環境測試 empty -> head
#   缺 (c) 是最常見的錯誤：autogenerate 對「重新命名欄位」
#   會產生 drop + add，那會遺失資料。
#
# 驗證方式：
#   flask db upgrade
#   pytest tests/test_db_portability.py
#   pytest tests/test_migration_roundtrip.py
# ============================================================

import logging
from logging.config import fileConfig

from alembic import context
from flask import current_app

#: Alembic Config 物件，提供對 alembic.ini 的存取。
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

logger = logging.getLogger("alembic.env")


def get_engine():
    """取得目前 Flask app 的 SQLAlchemy engine。

    同時相容 Flask-SQLAlchemy 3.x（engine 屬性）與
    2.x（get_engine()）。支援兩者可避免升級套件時
    migration 直接失效。

    順序說明：優先使用 3.x 的 `engine` 屬性。
    2.x 風格的 get_engine() 在 3.x 仍存在但已標記為
    deprecated，先呼叫它會在每次 migration 產生
    DeprecationWarning，把測試輸出淹沒。
    """
    db = current_app.extensions["migrate"].db
    try:
        # Flask-SQLAlchemy >= 3
        return db.engine
    except (TypeError, AttributeError):
        # Flask-SQLAlchemy < 3
        return db.get_engine()


def get_engine_url() -> str:
    """取得連線字串。

    注意：這個值會被寫進 alembic 設定並可能出現在 log。
    render_as_string(hide_password=False) 是 Alembic 執行所必需
    （需要真實密碼才能連線），因此請勿把 migration 的完整輸出
    貼到公開場合（SAI §11.2 Secret leak）。
    """
    try:
        return get_engine().url.render_as_string(hide_password=False).replace("%", "%%")
    except AttributeError:
        return str(get_engine().url).replace("%", "%%")


config.set_main_option("sqlalchemy.url", get_engine_url())
target_db = current_app.extensions["migrate"].db


def get_metadata():
    """取得要比對的 metadata。

    app/models/__init__.py 已匯總全部 7 張表（SAI §8）。
    若某張表沒被 import，它會從這裡消失，
    autogenerate 會誤判為「應該刪除該表」——
    這正是 models/__init__.py 禁止刪除 import 的原因。
    """
    if hasattr(target_db, "metadatas"):
        return target_db.metadatas[None]
    return target_db.metadata


def run_migrations_offline():
    """離線模式：產生 SQL 腳本，不實際連線。

    用途：需要 DBA 審核 SQL 才能套用的變更管制環境。
    本專案的 Cloud SQL 流程使用線上模式，
    但保留此路徑以備學校端的變更管制需求。
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=get_metadata(),
        literal_binds=True,
        compare_type=True,
        compare_server_default=True,
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    """線上模式：實際連線並執行 DDL。"""

    def process_revision_directives(context_, revision, directives):
        """無變更時取消產生空的 migration 檔（見檔頭 Error Handling）。"""
        if getattr(config.cmd_opts, "autogenerate", False):
            script = directives[0]
            if script.upgrade_ops.is_empty():
                directives[:] = []
                logger.info("偵測到 schema 無變更，未產生 migration 檔。")

    conf_args = current_app.extensions["migrate"].configure_args
    if conf_args.get("process_revision_directives") is None:
        conf_args["process_revision_directives"] = process_revision_directives

    # 統一設定，避免被 Flask-Migrate 的預設值覆蓋。
    conf_args["compare_type"] = True
    conf_args["compare_server_default"] = True
    conf_args["render_as_batch"] = True

    connectable = get_engine()

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=get_metadata(),
            **conf_args,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
