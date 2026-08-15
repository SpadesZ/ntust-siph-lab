# Cloud Run 部署指南

> 對應 SAI §21（Cloud Run Production Deployment）與附錄 B。
> 本文假設你已完成本機驗收（見 `docs/local-development.md`）。
>
> **開發階段不需要讀這份文件。** ADR-002 明定開發與內容驗收全部在
> 本機完成，不要求先建立任何 GCP 資源。只有在準備正式上線時才需要。

---

## 0. 前置條件（Production Migration Gate）

SAI §21.1 定義六道 gate，**全部通過**才能開始部署：

| Gate | 內容 | 如何確認 |
| --- | --- | --- |
| G1 Feature freeze | 本機驗收測試全通過 | `pytest` 全綠（含 `-m acceptance`） |
| G2 DB portability | Alembic 在 PostgreSQL staging 從 empty → head 成功 | `TEST_POSTGRES_URL=... pytest -m postgres` |
| G3 Data export | migration package 已產生且有 checksum | `python scripts/export_sqlite.py` |
| G4 Media export | 所有 uploads 有 object key 與 checksum | package 內的 `media_manifest.csv` |
| G5 Rollback point | 保留最後一份 known-good SQLite + uploads | `python scripts/backup_sqlite.py` |
| G6 Legacy No-Loss | difference_report 無 UNRESOLVED、sign-off 完成 | `python scripts/verify_migration.py` |

> **G6 未過就不得 cutover。** 這不是建議，是 ADR-011 的硬性條件。

---

## 1. GCP 資源基線（SAI §21.2）

```bash
export PROJECT_ID=<your-project>
export REGION=asia-east1          # 台灣使用者建議
export SERVICE=ntust-siph-lab

gcloud config set project "$PROJECT_ID"

# 啟用必要 API
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  sqladmin.googleapis.com \
  storage.googleapis.com \
  secretmanager.googleapis.com
```

### 1.1 Artifact Registry

```bash
gcloud artifacts repositories create siph-lab \
  --repository-format=docker --location="$REGION"
```

### 1.2 Cloud SQL for PostgreSQL（ADR-005）

```bash
gcloud sql instances create siph-lab-db \
  --database-version=POSTGRES_16 \
  --tier=db-f1-micro \
  --region="$REGION"

gcloud sql databases create siph_lab --instance=siph-lab-db
gcloud sql users create siph_app --instance=siph-lab-db --password=<strong-password>
```

> 成本提醒（SAI §24）：Cloud SQL 是**持續計費**的資源。請在建立後
> 立刻設定 budget alert。若成本不可接受，必須另開 ADR 評估其他
> managed PostgreSQL，**不得**私自改回 SQLite 或變更資料模型。

### 1.3 Cloud Storage（ADR-006）

```bash
gcloud storage buckets create "gs://${PROJECT_ID}-siph-media" \
  --location="$REGION" --uniform-bucket-level-access
```

公開讀取（若不使用 CDN/LB）：

```bash
gcloud storage buckets add-iam-policy-binding "gs://${PROJECT_ID}-siph-media" \
  --member=allUsers --role=roles/storage.objectViewer
```

> 若 bucket 保持私有，必須設定 `GCS_PUBLIC_BASE_URL` 指向 CDN 或
> Load Balancer，否則前台圖片會 403（`app/storage/gcs.py` 的維護契約）。

### 1.4 Service Account

```bash
gcloud iam service-accounts create siph-lab-run \
  --display-name="NTUST SiPh Lab Cloud Run"

SA="siph-lab-run@${PROJECT_ID}.iam.gserviceaccount.com"

# 只授予必要權限（最小權限原則）
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${SA}" --role=roles/cloudsql.client

gcloud storage buckets add-iam-policy-binding "gs://${PROJECT_ID}-siph-media" \
  --member="serviceAccount:${SA}" --role=roles/storage.objectAdmin

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${SA}" --role=roles/secretmanager.secretAccessor
```

### 1.5 Secret Manager（SAI §11.1、[S21]）

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))" \
  | gcloud secrets create siph-secret-key --data-file=-

printf '<db-password>' | gcloud secrets create siph-db-password --data-file=-
```

> **禁止**把 SECRET_KEY 或資料庫密碼寫進 Dockerfile、`.env` 或
> Cloud Run 的明文 env（SAI §11.2 Secret leak）。

---

## 2. 建置與推送 image

同一份 image 同時用於本機 compose 與 Cloud Run（ADR-004）。

```bash
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/siph-lab/${SERVICE}:$(git rev-parse --short HEAD)"

gcloud builds submit --tag "$IMAGE"
```

以 git SHA 作為 tag，讓每個 revision 可回溯到確切的程式碼版本。
**不要用 `latest`** ——那會讓 rollback 無法確定回到哪一版。

---

## 3. 建立 schema（先於部署）

SAI §21.3 第 1 點：**禁止**從 SQLite schema dump 建 PostgreSQL schema。
一律以 Alembic 建立。

```bash
# 以 Cloud SQL Auth Proxy 從本機連線
cloud-sql-proxy "${PROJECT_ID}:${REGION}:siph-lab-db" &

export DATABASE_URL="postgresql+psycopg://siph_app:<password>@127.0.0.1:5432/siph_lab"
export APP_ENV=local          # 只為了跑 migration，不啟動服務
flask db upgrade
```

確認 revision：

```bash
flask db current
```

---

## 4. 匯入資料與媒體（SAI §21.3）

```bash
# 1) 產生 package（在本機、對本機 SQLite 執行）
python scripts/export_sqlite.py --output ./migration-package

# 2) 先 dry-run，確認所有驗證通過且不留下資料
python scripts/import_postgres.py \
  --package ./migration-package \
  --database-url "$DATABASE_URL" --dry-run

# 3) 正式匯入
python scripts/import_postgres.py \
  --package ./migration-package \
  --database-url "$DATABASE_URL"

# 4) 媒體同步（先 dry-run）
export GCS_BUCKET="${PROJECT_ID}-siph-media"
python scripts/sync_media_to_gcs.py --dry-run
python scripts/sync_media_to_gcs.py --report ./media-sync-report.csv

# 5) 獨立複驗
python scripts/verify_migration.py
```

`import_postgres.py` 會在匯入後自動驗證 row count、內容 checksum、
unique slug、孤兒關聯與時間欄位，並**重設 PostgreSQL sequence**。

> Sequence 重設為什麼重要：帶著明確 id 插入 PostgreSQL 時 sequence
> 不會自動前進。若不處理，上線後管理者新增的第一筆內容會拿到 id=1
> 並立刻違反主鍵約束——而且這個錯誤在匯入當下完全看不出來。

---

## 5. 建立管理員帳號

```bash
# 仍透過 proxy 連線
flask admin create --username <admin> --generate
```

一次性密碼只顯示一次，請立即存入密碼管理器。

---

## 6. 部署 Cloud Run service

```bash
gcloud run deploy "$SERVICE" \
  --image="$IMAGE" \
  --region="$REGION" \
  --platform=managed \
  --service-account="$SA" \
  --add-cloudsql-instances="${PROJECT_ID}:${REGION}:siph-lab-db" \
  --allow-unauthenticated \
  --no-traffic \
  --set-env-vars="APP_ENV=production" \
  --set-env-vars="PUBLIC_BASE_URL=https://siph-lab.ntust.edu.tw" \
  --set-env-vars="DB_BACKEND=postgresql" \
  --set-env-vars="STORAGE_BACKEND=gcs" \
  --set-env-vars="GCS_BUCKET=${PROJECT_ID}-siph-media" \
  --set-env-vars="SESSION_COOKIE_SECURE=true" \
  --set-env-vars="LOG_LEVEL=INFO" \
  --set-env-vars="ROBOTS_POLICY=public" \
  --set-env-vars="WEB_CONCURRENCY=2" \
  --set-secrets="SECRET_KEY=siph-secret-key:latest" \
  --set-secrets="DB_PASSWORD=siph-db-password:latest" \
  --set-env-vars="DATABASE_URL=postgresql+psycopg://siph_app:\${DB_PASSWORD}@/siph_lab?host=/cloudsql/${PROJECT_ID}:${REGION}:siph-lab-db"
```

注意 `--no-traffic`：新 revision 先不接流量，通過 smoke test 才切換
（SAI §21.6）。

### ProductionConfig 的 fail-fast

`app/config.py` 會在啟動時拒絕以下情況，導致 revision 部署失敗
（這是刻意的——Cloud Run 會保留舊 revision 繼續服務）：

- 缺 `SECRET_KEY` / `DATABASE_URL` / `PUBLIC_BASE_URL`
- `PUBLIC_BASE_URL` 含 `localhost`
- `DATABASE_URL` 是 SQLite（ADR-004）
- `STORAGE_BACKEND` 不是 `gcs`（ADR-006）

---

## 7. Smoke test（切換 traffic 前的 gate）

```bash
REV_URL=$(gcloud run revisions describe <revision-name> \
  --region="$REGION" --format='value(status.url)')

python scripts/smoke_cloud.py "$REV_URL"
```

23 項檢查全過才繼續。特別注意兩項最容易出錯的：

- **canonical 指向本站網域** — `PUBLIC_BASE_URL` 忘了改是最常見疏漏，
  不會有任何錯誤畫面，只會安靜地讓 SEO 指向錯誤位置
- **session cookie 有 Secure 旗標** — 只有在真實 HTTPS 上才驗證得到

---

## 8. 切換 traffic

```bash
gcloud run services update-traffic "$SERVICE" --region="$REGION" --to-latest
```

再對正式網址跑一次 smoke test。

---

## 9. 自訂網域（SAI §21.5）

Google 目前對正式 custom domain 的首選是 **global external
Application Load Balancer**；Cloud Run 原生 domain mapping 仍屬
Preview/limited availability，**不列為本案 production baseline** [S22]。

在校方 DNS 完成前，可先用 `run.app` URL 做 staging，並把
`ROBOTS_POLICY=private` 避免暫時網址被索引。

---

## 10. 部署後檢查清單

- [ ] `/healthz` 回 200
- [ ] 首頁、人物頁、成果頁正常
- [ ] `/admin/login` 可存取，錯誤密碼觸發 rate limit
- [ ] 圖片從 GCS 正常載入（非 403）
- [ ] canonical / sitemap 使用正式網域
- [ ] Search Console 提交 sitemap
- [ ] 重啟一個 instance 後資料仍在（驗證非依賴 container filesystem）
- [ ] 舊 Google Sites **尚未**關閉（等 sign-off，見 `deploy/migration-runbook.md`）

---

## 相關文件

- `deploy/migration-runbook.md` — 完整 cutover 與 rollback 程序
- `deploy/service.yaml` — 宣告式 Cloud Run 設定（選用）
- `docs/cloudrun-deployment.md` — 環境變數逐項說明
