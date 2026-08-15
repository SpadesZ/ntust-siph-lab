# ============================================================
# NTUST SiPh Lab - Utils Package
#
# 檔案路徑：app/utils/__init__.py
# 建立日期：2026-08-14 / 版本：v1.0
#
# 模組定位：
#   純函式工具的匯總層。此套件內的模組「不得」依賴 request
#   context、不得存取 storage backend、不得 render HTML。
#   唯一允許的外部相依是 app.extensions.db（僅 slugs 的唯一性檢查）。
#
# 上下游：
#   blueprints / services / scripts -> app.utils.*
#
# 子模組：
#   slugs      - slug 產生與唯一性
#   validators - 欄位格式驗證與正規化
#   dates      - UTC -> Asia/Taipei 顯示轉換
#
# 維護契約：
#   新增子模組時保持「無副作用純函式」原則。
#   一旦某個工具需要 app context，它就應該搬到 services/。
#
# 驗證方式：
#   pytest tests/test_slugs.py tests/test_validators.py
# ============================================================
