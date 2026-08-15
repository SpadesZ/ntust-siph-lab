# ============================================================
# NTUST SiPh Lab - Services Package
#
# 檔案路徑：app/services/__init__.py
# 建立日期：2026-08-14 / 版本：v1.0
#
# 模組定位（SAI §9.1 Layering）：
#   Service 負責內容發布、graduate transition、redirect、
#   SEO metadata 與 backup status。
#   Service「不得 render HTML」，也不得依賴 request context
#   （current_app.config 除外，因為那是設定而非請求狀態）。
#
# 交易邊界：
#   Service 是唯一被允許呼叫 db.session.commit() 的層。
#   Model 與 repository 都不 commit，確保「內容變更 + Redirect +
#   AuditLog」能在同一個 transaction 內完成（SAI §9.3）。
#
# 子模組：
#   person_service, research_service, seo_service, schema_service,
#   media_service, publish_validator, settings_service, health_service
#
# 驗證方式：
#   pytest tests/
# ============================================================
