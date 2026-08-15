# ============================================================
# NTUST SiPh Lab - Gunicorn Configuration
#
# 上下游：
#   Dockerfile CMD / docker-compose command
#       -> gunicorn -c gunicorn.conf.py wsgi:app
#       -> wsgi.py -> create_app() -> Flask app
#   Cloud Run -> 同一份設定（PORT 由平台注入）
#
# 檔案路徑：
#   gunicorn.conf.py
#
# 建立日期：2026-08-14
# 最後重大修改：2026-08-14
# 版本：v1.0
#
# 模組定位與責任邊界：
#   正式環境的 WSGI server 設定。SAI AC-20 明確要求
#   「production 啟動使用 Gunicorn，不是 flask run」，
#   Flask 官方文件亦說明不得使用 development server [S8]。
#
#   責任邊界（不得做的事）：
#     - 不得在此讀取應用設定或連線資料庫。
#     - 不得在此執行 migration（那是明確的部署步驟）。
#
# 輸入 -> 處理 -> 輸出 Pipeline：
#   環境變數（PORT / WEB_CONCURRENCY / GUNICORN_TIMEOUT）
#     -> 本檔的模組層級變數 -> gunicorn master process
#     -> worker processes 服務 HTTP
#
# 主要設定：
#   bind / workers / threads / worker_class / timeout /
#   accesslog / errorlog / max_requests
#
# 依賴套件：gunicorn
#
# 環境變數：
#   PORT             - 監聽埠。Cloud Run 會自動注入（預設 8080）。
#   WEB_CONCURRENCY  - worker 數量。
#   GUNICORN_THREADS - 每個 worker 的執行緒數。
#   GUNICORN_TIMEOUT - 請求逾時秒數。
#   LOG_LEVEL        - gunicorn 自身的日誌等級。
#
# 資料庫使用方式：
#   本檔不接觸資料庫。但 worker 數量會直接影響資料庫連線數：
#   總連線上限 ≈ workers × (pool_size + max_overflow)。
#   預設 2 workers × (5 + 2) = 14 條，遠低於 Cloud SQL
#   最小規格的上限，安全。調高 workers 時必須同步檢查
#   config.py 的 DB_POOL_SIZE（見下方維護契約）。
#
# Error Handling / Fallback：
#   環境變數格式錯誤時使用預設值，不讓容器啟動失敗 ——
#   服務可用性優先於設定精確度。
#
# 特殊機制（worker 模型選擇）：
#   使用同步 worker（預設 sync）搭配多執行緒，而非 gevent/eventlet。
#   為什麼：
#     1. 本應用是 server-rendered 的 CRUD 網站，
#        沒有長連線或高併發需求（研究室官網流量極低）。
#     2. SQLAlchemy 的同步驅動與 gevent 的 monkey patching
#        搭配容易產生難以診斷的連線問題。
#     3. sync + threads 對 I/O 等待（DB 查詢）已足夠，
#        且行為可預測。
#
# 特殊機制（Cloud Run 的 PORT）：
#   Cloud Run 透過 PORT 環境變數告知容器要監聽哪個埠，
#   且必須綁定 0.0.0.0（非 127.0.0.1），否則平台的健康檢查
#   會失敗並反覆重啟 instance。
#
# 已知限制與禁止事項：
#   1. 禁止在此啟用 preload_app 而不重新檢視資料庫連線 ——
#      preload 會讓 fork 出的 worker 共用同一個連線池物件，
#      造成 "connection already closed" 類錯誤。
#   2. 禁止把 accesslog 寫入檔案；Cloud Run 的檔案系統不持久 [S17]，
#      日誌必須走 stdout/stderr（SAI §19）。
#
# 維護契約：
#   調整 workers 時必須同步評估：
#     (a) config.py 的 DB_POOL_SIZE / DB_MAX_OVERFLOW
#     (b) Cloud SQL instance 的最大連線數
#   否則會在流量上升時出現連線耗盡。
#
# 驗證方式：
#   gunicorn -c gunicorn.conf.py wsgi:app
#   curl -i http://localhost:8000/healthz
#   docker compose up 後檢查容器日誌
# ============================================================

import os


def _env_int(key: str, default: int) -> int:
    """讀取整數環境變數；格式錯誤時退回預設值（見檔頭 Fallback）。"""
    try:
        return int(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


# --- 綁定位址 ---
# 必須是 0.0.0.0（見檔頭「特殊機制（Cloud Run 的 PORT）」）。
_port = _env_int("PORT", 8000)
bind = f"0.0.0.0:{_port}"

# --- Worker 模型 ---
# 研究室官網流量低，2 個 worker 足以提供冗餘（單一 worker
# 在處理慢請求時會阻塞健康檢查）。
workers = _env_int("WEB_CONCURRENCY", 2)
threads = _env_int("GUNICORN_THREADS", 4)
worker_class = "gthread"

# --- 逾時 ---
# 30 秒足夠涵蓋最慢的操作（圖片重新編碼 + 上傳到 GCS）。
timeout = _env_int("GUNICORN_TIMEOUT", 30)
graceful_timeout = 30
# keep-alive 略長於一般 Load Balancer 的閒置時間，
# 避免 LB 重用已被 gunicorn 關閉的連線而產生 502。
keepalive = 65

# --- Worker 回收 ---
# 定期重啟 worker 以釋放可能的記憶體累積（Pillow 處理大圖後尤其明顯）。
# jitter 避免所有 worker 同時重啟造成瞬間無法服務。
max_requests = _env_int("GUNICORN_MAX_REQUESTS", 1000)
max_requests_jitter = 100

# --- 日誌（SAI §19：stdout/stderr，由 Docker/Cloud Logging 收集） ---
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("LOG_LEVEL", "info").lower()

# 存取日誌格式。刻意「不」記錄 request body 或 cookie
# （SAI §19：No secret logging）。
access_log_format = '%(h)s "%(r)s" %(s)s %(b)s %(M)sms "%(f)s" "%(a)s"'

# --- 其他 ---
# 見檔頭「已知限制 1」：不啟用 preload_app。
preload_app = False

# 進程名稱，方便在容器內以 ps 辨識。
proc_name = "ntust-siph-lab"

# Cloud Run 位於反向代理之後，需信任 X-Forwarded-* 標頭
# 才能取得正確的 client IP（供 rate limit 與 AuditLog 使用）。
forwarded_allow_ips = os.environ.get("FORWARDED_ALLOW_IPS", "*")
