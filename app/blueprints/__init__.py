# ============================================================
# NTUST SiPh Lab - Blueprints Package
#
# 檔案路徑：app/blueprints/__init__.py
# 建立日期：2026-08-14 / 版本：v1.0
#
# 模組定位：
#   三個 blueprint 的容器：public（公開頁）、auth（/admin/login）、
#   admin（CMS）。實際註冊在 app/__init__.py 的 create_app()。
#
# 責任邊界（SAI §9.1）：
#   Blueprint 只負責解析 request、權限檢查、呼叫 service、
#   render template。不得把複雜 DB 邏輯寫在 route。
#
# 維護契約：
#   新增 blueprint 必須同時在 create_app() 註冊並補上 url_prefix，
#   且公開頁必須同步處理 title/description/canonical/sitemap
#   （SAI §23.1 Agent 開發規則）。
# ============================================================
