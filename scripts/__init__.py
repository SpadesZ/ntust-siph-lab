# ============================================================
# NTUST SiPh Lab - Scripts Package
#
# 檔案路徑：scripts/__init__.py
# 建立日期：2026-08-14 / 版本：v1.0
#
# 模組定位（SAI §9.4 目錄樹）：
#   維運與遷移腳本的容器。這些腳本「不是」應用程式的一部分，
#   而是人工執行的維運工具：
#     seed_from_google_sites - 母站內容匯入（LC-001~LC-019）
#     legacy_baseline        - 母站盤點資料（單一真實來源）
#     create_admin           - 管理員帳號建立
#     backup_sqlite          - 備份與還原演練
#     export_sqlite          - 產生 migration package
#     import_postgres        - 匯入 PostgreSQL
#     sync_media_to_gcs      - 媒體同步至 Cloud Storage
#     verify_migration       - 遷移驗證與 difference report
#     smoke_cloud            - Cloud Run 部署後煙霧測試
#     generate_og_image      - 產生預設分享預覽圖（OG image）
#
# 為什麼需要 __init__.py：
#   讓 `from scripts import legacy_baseline` 可用。
#   seed 腳本與測試都需要匯入 legacy_baseline 的盤點資料，
#   若沒有套件標記，兩者會各自複製一份常數而產生漂移。
#
# 責任邊界：
#   腳本可以呼叫 app.services，但 app/ 底下的程式碼
#   「不得」import scripts/（單向相依）。
#   唯一例外是 app/cli.py 的 `flask seed legacy` 指令，
#   它在函式內部延遲 import，不會造成模組載入期的循環相依。
#
# 維護契約：
#   新增腳本時必須附上完整檔頭註解，並在
#   docs/local-development.md 或 docs/cloudrun-deployment.md
#   說明使用時機。
#
# 驗證方式：
#   pytest tests/test_legacy_migration.py
# ============================================================
