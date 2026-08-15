# ============================================================
# NTUST SiPh Lab - Repositories Package
#
# 檔案路徑：app/repositories/__init__.py
# 建立日期：2026-08-14 / 版本：v1.0
#
# 模組定位（SAI §9.4「DB-portable query boundary」）：
#   所有列表/篩選查詢集中於此，確保：
#     1. 查詢一律走 SQLAlchemy ORM，不出現 raw SQL
#        （SAI §10.2 可攜契約）。
#     2. 不依賴 SQLite-only 函式；同一段程式在 PostgreSQL 也成立。
#
# 責任邊界：
#   Repository 只讀不寫、不 commit、不寫 AuditLog。
#   任何寫入都必須經過 service 層。
#
# 子模組：
#   people, research, settings
#
# 驗證方式：
#   pytest tests/test_db_portability.py
# ============================================================
