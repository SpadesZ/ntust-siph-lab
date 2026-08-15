# ============================================================
# NTUST SiPh Lab - Application Container Image
#
# 上下游：
#   docker compose build / gcloud builds submit
#       -> 本 Dockerfile
#       -> requirements.txt（相依安裝）
#       -> app/ + wsgi.py + gunicorn.conf.py
#       -> 同一份 image 同時用於 local compose 與 Cloud Run
#
# 檔案路徑：
#   Dockerfile
#
# 建立日期：2026-08-14
# 最後重大修改：2026-08-14
# 版本：v1.0
#
# 模組定位與責任邊界：
#   ADR-004 明定「應用維持同一 Docker image」，SAI §25 進一步
#   要求 local 與 production「不是兩套網站」，環境差異只允許
#   出現在 configuration、database connection、storage adapter
#   與 deployment tooling。因此本 image 不含任何環境專屬邏輯。
#
#   責任邊界（不得做的事）：
#     - 不得把 .env、instance/、uploads/、backups/ 打包進 image
#       （見 .dockerignore）。
#     - 不得在 image 中內建資料庫檔案。
#     - 不得在建置階段執行 migration 或 seed。
#
# 建置 -> 執行 Pipeline：
#   builder stage：安裝編譯相依 -> pip install 到 /install
#   runtime stage：只複製已安裝套件與應用程式碼
#     -> 建立非 root 使用者 -> CMD gunicorn
#
# 主要階段：
#   builder  - 安裝相依（含編譯工具，不進入最終 image）
#   runtime  - 最終執行環境
#
# 依賴：python:3.12-slim
#
# 環境變數（執行期）：
#   APP_ENV / SECRET_KEY / DATABASE_URL / STORAGE_BACKEND /
#   UPLOAD_DIR / PUBLIC_BASE_URL / PORT
#   （完整清單見 .env.example 與 SAI 附錄 B）
#
# 資料持久化：
#   image 本身不含資料。
#     local      -> docker-compose 以 bind mount 掛入
#                   instance/、uploads/、backups/（AC-13）
#     production -> Cloud SQL + Cloud Storage（ADR-005/006）；
#                   Cloud Run 的 container filesystem 不具持久性 [S17]
#
# Error Handling / Fallback：
#   HEALTHCHECK 呼叫 /healthz。失敗時 Docker 會標記容器 unhealthy，
#   compose 可據此重啟。Cloud Run 另有自己的健康檢查機制。
#
# 特殊機制（多階段建置）：
#   Pillow 與 psycopg 需要編譯工具與標頭檔。若在單階段建置，
#   這些工具會留在最終 image 中，體積增加約 250 MB
#   並擴大攻擊面。多階段建置讓最終 image 只含執行期所需。
#
# 特殊機制（非 root 執行）：
#   容器以非 root 使用者 appuser 執行。即使應用被入侵，
#   攻擊者也無法寫入系統目錄或安裝套件。
#   uploads 目錄的擁有者一併設為 appuser，
#   否則 local backend 會因權限不足而無法寫入。
#
# 已知限制與禁止事項：
#   1. 禁止在 image 中使用 flask run（AC-20）。
#   2. 禁止把 SECRET_KEY 等機密以 ENV 寫進 Dockerfile ——
#      那會永久留在 image layer 中（SAI §11.2 Secret leak）。
#      正式環境一律由 Secret Manager 注入 [S21]。
#   3. 禁止在 production 讓 uploads 寫入 container filesystem。
#
# 維護契約：
#   1. 升級基底映像時必須重跑完整測試與 flask db upgrade。
#   2. 新增系統層相依（例如影像格式的原生函式庫）時，
#      必須同時加到 builder 與 runtime 兩個階段。
#
# 驗證方式：
#   docker build -t ntust-siph-lab .
#   docker compose up
#   curl -i http://localhost:8000/healthz
# ============================================================

# ------------------------------------------------------------
# Stage 1: builder
# ------------------------------------------------------------
FROM python:3.12-slim AS builder

# 編譯 Pillow 與 psycopg 所需的工具與標頭檔。
# 這些不會進入最終 image（見檔頭「特殊機制（多階段建置）」）。
RUN apt-get update && apt-get install --no-install-recommends -y \
        build-essential \
        libpq-dev \
        libjpeg-dev \
        zlib1g-dev \
        libwebp-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

COPY requirements.txt ./

# --prefix 讓套件裝在可整批複製的目錄，避免複製整個 site-packages。
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir --prefix=/install -r requirements.txt


# ------------------------------------------------------------
# Stage 2: runtime
# ------------------------------------------------------------
FROM python:3.12-slim AS runtime

# 執行期所需的原生函式庫（不含編譯工具）。
RUN apt-get update && apt-get install --no-install-recommends -y \
        libpq5 \
        libjpeg62-turbo \
        libwebp7 \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Python 執行環境設定：
#   PYTHONDONTWRITEBYTECODE - 不產生 .pyc，保持容器檔案系統乾淨
#   PYTHONUNBUFFERED        - 日誌即時輸出（SAI §19：Docker 收集 stdout）
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    APP_ENV=production \
    PORT=8000

# 從 builder 複製已安裝的套件。
COPY --from=builder /install /usr/local

WORKDIR /app

# 建立非 root 使用者（見檔頭「特殊機制（非 root 執行）」）。
RUN useradd --create-home --shell /usr/sbin/nologin --uid 10001 appuser

# 複製應用程式碼。.dockerignore 已排除 .env、instance、uploads、
# backups、.venv、.git 等不應進入 image 的內容。
COPY --chown=appuser:appuser app/ ./app/
COPY --chown=appuser:appuser migrations/ ./migrations/
COPY --chown=appuser:appuser scripts/ ./scripts/
COPY --chown=appuser:appuser wsgi.py gunicorn.conf.py ./

# 建立資料目錄並交給 appuser。
# local compose 會以 bind mount 覆蓋這些路徑（AC-13）；
# production 則使用 Cloud SQL 與 GCS，這些目錄僅作為
# 短生命週期暫存（SAI §21.4）。
RUN mkdir -p /app/instance /app/uploads/people /app/uploads/research \
             /app/uploads/site /app/backups \
    && chown -R appuser:appuser /app/instance /app/uploads /app/backups

USER appuser

EXPOSE 8000

# 健康檢查（SAI §19、附錄 A：/healthz 回 200/503）。
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/healthz" || exit 1

# AC-20：production 使用 Gunicorn，不是 flask run。
CMD ["gunicorn", "-c", "gunicorn.conf.py", "wsgi:app"]
