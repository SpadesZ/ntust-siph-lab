# ============================================================
# NTUST SiPh Lab - WSGI Entry Point
#
# 上下游：
#   gunicorn -c gunicorn.conf.py wsgi:app
#       -> create_app() -> Flask app -> HTTP 服務
#   Cloud Run container CMD -> 同一個進入點（SAI §25：
#       同一套程式碼，環境差異只在設定）
#
# 檔案路徑：
#   wsgi.py
#
# 建立日期：2026-08-14
# 最後重大修改：2026-08-14
# 版本：v1.0
#
# 模組定位與責任邊界：
#   WSGI server 的唯一進入點。SAI AC-20 要求
#   「production 啟動使用 Gunicorn，不是 flask run」，
#   本檔即是那條啟動路徑。
#
#   責任邊界（不得做的事）：
#     - 不得在此加入任何設定邏輯（全部在 app/config.py）。
#     - 不得在此執行 migration 或 seed
#       （那些是明確的維運步驟，不應在每次 worker 啟動時發生）。
#
# 輸入 -> 處理 -> 輸出 Pipeline：
#   APP_ENV 環境變數 -> create_app() -> module-level `app` 物件
#   -> gunicorn 以多個 worker process 匯入此模組
#
# 主要物件：
#   app - WSGI application callable
#
# 依賴套件：
#   app（本專案）、python-dotenv（僅 local）
#
# 環境變數：
#   APP_ENV - 決定使用哪個 Config（未設定時為 local）
#   其餘見 app/config.py
#
# 資料庫使用方式：
#   本檔不執行查詢。schema 由 `flask db upgrade` 事先建立。
#
# Error Handling / Fallback：
#   若 ProductionConfig 的必要設定缺失，create_app() 會拋
#   RuntimeError，導致 worker 啟動失敗。這是刻意的 fail-fast：
#   Cloud Run 會保留舊 revision 繼續服務（SAI §21.6 rollback），
#   比用不安全的預設值上線好。
#
# 特殊機制（.env 載入）：
#   只在非 production 載入 .env 檔案。production 的設定一律
#   來自 Cloud Run 環境變數與 Secret Manager [S21]，
#   若同時存在 .env 會造成「以為改了設定卻沒生效」的困惑。
#
# 已知限制與禁止事項：
#   1. 禁止在此呼叫 app.run()（那是 development server，違反 AC-20）。
#   2. 禁止在 import 時連線資料庫。
#
# 維護契約：
#   若新增啟動前必須完成的初始化，應加在 create_app() 內，
#   而不是這裡 —— 否則 flask CLI 與測試不會執行到。
#
# 驗證方式：
#   gunicorn -c gunicorn.conf.py wsgi:app
#   curl -i http://localhost:8000/healthz
# ============================================================

from __future__ import annotations

import os

# 只在非 production 載入 .env（見檔頭「特殊機制」）。
if os.environ.get("APP_ENV", "local").lower() != "production":
    try:
        from dotenv import load_dotenv

        # 依序嘗試 .env.local 與 .env；先載入的優先。
        for candidate in (".env.local", ".env"):
            if os.path.exists(candidate):
                load_dotenv(candidate, override=False)
    except ImportError:  # pragma: no cover - dotenv 為選用相依
        pass

from app import create_app  # noqa: E402 - 必須在 load_dotenv 之後

#: WSGI application。gunicorn 以 `wsgi:app` 參照此物件。
app = create_app()
