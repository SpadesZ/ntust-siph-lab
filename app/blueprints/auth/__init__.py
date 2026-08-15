# ============================================================
# NTUST SiPh Lab - auth blueprint package
#
# 檔案路徑：app/blueprints/auth/__init__.py
# 建立日期：2026-08-14 / 版本：v1.0
#
# 模組定位：
#   auth blueprint 的套件標記。實際 route 定義在同目錄的
#   routes.py，由 app/__init__.py 的 _register_blueprints() 註冊。
#
# 責任邊界（SAI §9.1）：
#   Route 只負責解析 request、權限檢查、呼叫 service、render
#   template；不得把複雜 DB 邏輯寫在 route 內。
#
# 維護契約：
#   不要在此檔 import routes —— 那會在 app/__init__.py 尚未
#   完成 extension 初始化時觸發 model 載入，造成循環相依。
# ============================================================
