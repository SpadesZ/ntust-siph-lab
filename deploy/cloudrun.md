# Cloud Run 部署指南

> 對應 SAI §21（Cloud Run Production Deployment）與附錄 B。
> 本文假設你已完成本機驗收（見 `docs/local-development.md`）。
>
> **開發階段不需要讀這份文件。** ADR-002 明定開發與內容驗收全部在
> 本機完成，不要求先建立任何 GCP 資源。只有在準備正式上線時才需要。

> **本文已於 2026-08-17 依實際部署改寫。**
> 資料庫使用 **Neon 免費方案**，不是 Cloud SQL（決策見 **ADR-013**）。
> 目標是整體月費 ≈ NT$0；每一個資源選擇都以「留在免費額度內」為準。
> 已上線站台的完整盤點與維運指令見 **`docs/HANDOFF.md`**。

---

## 0. 前置條件（Production Migration Gate）

SAI §21.1 定義六道 gate，**全部通過**才能開始部署：

| Gate | 內容 | 如何確認 |
| --- | --- | --- |
| G1 Feature freeze | 本機驗收測試全通過 | `pytest` 全綠（含 `-m acceptance`） |
| G2 DB portability | Alembic 在 PostgreSQL 從 empty → head 成功 | `TEST_POSTGRES_URL=... pytest -m postgres`（⚠️ 必須指向**測試**資料庫，見 §3.1） |
| G3 Data export | migration package 已產生且有 checksum | `python scripts/export_sqlite.py` |
| G4 Media export | 所有 uploads 有 object key 與 checksum | package 內的 `media_manifest.csv` |
| G5 Rollback point | 保留最後一份 known-good SQLite + uploads | `python scripts/backup_sqlite.py` |
| G6 Legacy No-Loss | difference_report 無 UNRESOLVED、sign-off 完成 | `python scripts/verify_migration.py` |

> **G6 未過就不得 cutover。** 這不是建議，是 ADR-011 的硬性條件。

---

## 1. GCP 資源基線（SAI §21.2）

```bash
export PROJECT_ID=ntust-siph-lab   # 實際使用的專案
export REGION=asia-east1           # 台灣使用者建議
export SERVICE=ntust-siph-lab

gcloud config set project "$PROJECT_ID"

# 啟用必要 API
# 注意：刻意「不」啟用 sqladmin —— 本案不使用 Cloud SQL（ADR-013）。
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  storage.googleapis.com \
  secretmanager.googleapis.com \
  cloudbuild.googleapis.com
```

### 1.1 Artifact Registry

```bash
gcloud artifacts repositories create siph-lab \
  --repository-format=docker --location="$REGION"
```

> 免費額度為 **0.5 GB，整個帳單帳戶共用**（不是每專案各 0.5 GB）。
> 本站映像約 128 MB。累積舊映像會超額（約 US$0.10/GB/月），
> 定期清理未被任何 revision 或 Cloud Run Job 引用的映像。
> **清理前務必先確認沒有 Cloud Run Job 仍引用該映像** ——
> job 用的映像常常「看起來很舊」，刪掉會讓排程任務靜默失效。

### 1.2 PostgreSQL：Neon 免費方案（ADR-013）

**本案不建立 Cloud SQL。** Cloud SQL 沒有免費方案，最小的
`db-f1-micro` 約 US$7–10/月且為持續計費（零流量仍照收），
與 Cloud Run 的 scale-to-zero 相反，會成為架構中唯一的固定支出。

改用 **Neon 免費方案**（永久免費、0.5 GB、閒置自動縮到零）：

1. 前往 https://neon.tech 註冊（免信用卡），建立專案
2. 區域選 **`ap-southeast-1`（新加坡）** 或 `ap-northeast-1`（東京），離台灣最近
3. 複製 connection string

> **必須使用 direct 端點，不要用 pooler。**
> Neon 的 `-pooler` 端點是 PgBouncer transaction 模式，而 psycopg3 會自動
> 建立 prepared statement，兩者相衝會產生 `prepared statement already exists`。
> 把主機名稱中的 `-pooler` 拿掉即為直連端點。
>
> 連線數不是問題：`maxScale=4` × (`DB_POOL_SIZE=5` + `DB_MAX_OVERFLOW=2`)
> = 最多 28 條連線，遠低於 Neon 免費方案的上限。

> **未選 Supabase 的理由**：其免費方案在無流量滿 7 天會暫停專案。
> 研究室網站寒暑假可能低流量，暫停會造成非預期停機。

> 若日後要改回 Cloud SQL，資料模型與 Alembic migration 完全不需變更
> （ADR-013 §4），`scripts/import_postgres.py` 可直接重用。

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
# 不需要 roles/cloudsql.client —— 本案不使用 Cloud SQL（ADR-013）。
gcloud storage buckets add-iam-policy-binding "gs://${PROJECT_ID}-siph-media" \
  --member="serviceAccount:${SA}" --role=roles/storage.objectAdmin
```

Secret 的存取權**逐一授權**，不在專案層級給
`roles/secretmanager.secretAccessor`（那會讓該服務帳號能讀取
專案內所有 secret，包含未來新增的）：

```bash
for S in siph-secret-key siph-audit-ip-salt siph-database-url; do
  gcloud secrets add-iam-policy-binding "$S" \
    --member="serviceAccount:${SA}" --role=roles/secretmanager.secretAccessor
done
```

### 1.5 Secret Manager（SAI §11.1、[S21]）

本案需要**三個** secret：

```bash
# 1) Flask SECRET_KEY
python -c "import secrets; print(secrets.token_urlsafe(48), end='')" \
  | gcloud secrets create siph-secret-key --data-file=-

# 2) AuditLog 的 IP 雜湊 salt（SAI §8.8）
#    使用固定預設值會讓 IPv4 雜湊可被完整反查
python -c "import secrets; print(secrets.token_urlsafe(48), end='')" \
  | gcloud secrets create siph-audit-ip-salt --data-file=-

# 3) 完整的資料庫連線字串
#    與 Cloud SQL 版本不同：Neon 的 host/user/password 都在同一條字串裡，
#    因此整條存成一個 secret，而不是只存密碼。
#    注意 scheme 要用 postgresql+psycopg://（psycopg3），且主機不含 -pooler。
printf 'postgresql+psycopg://<user>:<pw>@<host>.aws.neon.tech/neondb?sslmode=require' \
  | gcloud secrets create siph-database-url --data-file=-
```

> **禁止**把 SECRET_KEY 或資料庫密碼寫進 Dockerfile、`.env` 或
> Cloud Run 的明文 env（SAI §11.2 Secret leak）。
>
> 用 `printf` 而非 `echo`：`echo` 會多帶一個換行字元，導致 secret 值
> 尾端多出 `\n`，連線字串會因此失效且錯誤訊息難以聯想到這個原因。

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

Neon 可直接從本機連線，**不需要 Cloud SQL Auth Proxy**：

```bash
export DATABASE_URL="$(gcloud secrets versions access latest --secret=siph-database-url)"
export APP_ENV=local FLASK_APP=wsgi.py   # 只為了跑 migration，不啟動服務
flask db upgrade
```

確認 revision：

```bash
flask db current
```

> `APP_ENV=local` 指向 PostgreSQL 是安全的：`create_app()` 會呼叫
> `config.engine_options_for()` 依實際 dialect 重算 engine options，
> 把 `LocalConfig` 類別層級的 SQLite 專屬 `connect_args`
> （`check_same_thread`、`timeout`）剝除，psycopg 不會收到它們。

### 3.1 另建一個測試資料庫（G2 用）

**`tests/test_db_portability.py` 會執行 `DROP SCHEMA IF EXISTS public CASCADE`。**
若把 `TEST_POSTGRES_URL` 指向正式資料庫，**正式資料會被刪光**。

因此在同一個 Neon 專案下另開一個資料庫供 G2 使用：

```sql
CREATE DATABASE siph_test;
```

```bash
# 注意：把資料庫名稱換成 siph_test，不要指向正式庫
export TEST_POSTGRES_URL="${DATABASE_URL/\/neondb?//siph_test?}"
pytest -m postgres
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
# DATABASE_URL 仍指向 Neon（不需要 proxy）
flask admin create --username <admin> --generate
```

一次性密碼只顯示一次，請立即存入密碼管理器。

> 密碼以 scrypt 單向雜湊儲存，**遺失後無法查詢，只能重設**：
> `flask admin reset-password --username <admin> --generate`

---

## 6. 部署 Cloud Run service

```bash
gcloud run deploy "$SERVICE" \
  --image="$IMAGE" \
  --region="$REGION" \
  --platform=managed \
  --service-account="$SA" \
  --allow-unauthenticated \
  --no-traffic --tag=candidate \
  --port=8000 --cpu=1 --memory=512Mi \
  --min-instances=0 --max-instances=4 --concurrency=40 --timeout=60 \
  --cpu-boost \
  --set-env-vars="APP_ENV=production,PUBLIC_BASE_URL=https://<你的網域>,DB_BACKEND=postgresql,STORAGE_BACKEND=gcs,GCS_BUCKET=${PROJECT_ID}-siph-media,SESSION_COOKIE_SECURE=true,PERMANENT_SESSION_LIFETIME_HOURS=8,LOG_LEVEL=INFO,ROBOTS_POLICY=private,ENABLE_LLMS_TXT=false,WEB_CONCURRENCY=2,DB_POOL_SIZE=5,DB_MAX_OVERFLOW=2" \
  --set-secrets="SECRET_KEY=siph-secret-key:latest,AUDIT_IP_SALT=siph-audit-ip-salt:latest,DATABASE_URL=siph-database-url:latest"
```

與 Cloud SQL 版本的三個差異：

1. **沒有 `--add-cloudsql-instances`** —— Neon 走公網 TLS，不需要掛載 unix socket。
2. **`DATABASE_URL` 直接來自 Secret Manager**，不再由密碼組合而成。
3. **多了 `AUDIT_IP_SALT`**（SAI §8.8）。

> **`--min-instances=0` 是零成本的關鍵。**
> 沒有流量時不計費。為了避免冷啟動而調成 1，費用會從 NT$0 變成每月數百元。

> **`PUBLIC_BASE_URL` 的雞生蛋問題**：它必須是服務網址，但網址要部署後才知道。
> Cloud Run 的網址格式是可預測的：
> `https://<服務名稱>-<專案編號>.<區域>.run.app`
> 先用推算值部署，部署完成後核對輸出的 Service URL 是否相符即可。

注意 `--no-traffic --tag=candidate`：新 revision 先不接流量，
通過 smoke test 才切換（SAI §21.6）。`--tag` 讓你能直接對該 revision 測試。

**首次部署**時服務尚不存在、沒有舊 revision 可保護，可省略
`--no-traffic --tag=candidate` 直接上線。

### ProductionConfig 的 fail-fast

`app/config.py` 會在啟動時拒絕以下情況，導致 revision 部署失敗
（這是刻意的——Cloud Run 會保留舊 revision 繼續服務）：

- 缺 `SECRET_KEY` / `DATABASE_URL` / `PUBLIC_BASE_URL`
- `PUBLIC_BASE_URL` 含 `localhost`
- `DATABASE_URL` 是 SQLite（ADR-004）
- `STORAGE_BACKEND` 不是 `gcs`（ADR-006）

> 改用 Neon **不會**放寬這些檢查：資料庫仍是 PostgreSQL，
> 「禁止 SQLite」與「必須用 GCS」完全成立（ADR-013 §4）。

---

## 7. Smoke test（切換 traffic 前的 gate）

上一步的 `--tag=candidate` 會印出 candidate 專屬網址，直接對它測試：

```bash
python scripts/smoke_cloud.py "https://candidate---<service>-<hash>-<code>.a.run.app"
```

特別注意兩項最容易出錯的：

- **canonical 指向本站網域** — `PUBLIC_BASE_URL` 忘了改是最常見疏漏，
  不會有任何錯誤畫面，只會安靜地讓 SEO 指向錯誤位置
- **session cookie 有 Secure 旗標** — 只有在真實 HTTPS 上才驗證得到

### 兩種「預期中的失敗」，不需要修

| 失敗項目 | 何時出現 | 說明 |
| --- | --- | --- |
| `robots.txt 禁止 /admin` | `ROBOTS_POLICY=private` 時 | private 輸出全站 `Disallow: /`，範圍更嚴格。改成 `public` 後自動通過 |
| `canonical / sitemap 指向本站網域` | 測試 `candidate---` 網址時 | canonical 指向正式網址才是對的，不該跟著暫時網址跑 |

因此對 candidate 測試時的合理成績是 **22/25**，
切換 traffic 後對正式網址重測應為 **24/25**（第 25 項為上表第一列，
開放索引後成為 25/25）。

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

- [ ] `/health` 回 200
      （**不是** `/healthz` —— `/healthz` 是 Cloud Run 保留路徑，
      從公開網址會拿到 Google Frontend 的 404，請求不會進到容器。
      容器內的 probe 與 Dockerfile HEALTHCHECK 仍用 `/healthz`。見 ADR-014）
- [ ] 首頁、人物頁、成果頁正常
- [ ] `/admin/login` 可存取，錯誤密碼觸發 rate limit
- [ ] 圖片從 GCS 正常載入（非 403）
- [ ] canonical / sitemap 使用正式網域
- [ ] Search Console 提交 sitemap
- [ ] 重啟一個 instance 後資料仍在（驗證非依賴 container filesystem）
- [ ] 舊 Google Sites **尚未**關閉（等 sign-off，見 `deploy/migration-runbook.md`）
- [ ] 已設定 budget alert（見 §11）

---

## 11. 成本與免費額度（SAI §24）

本案的架構選擇以「留在免費額度內」為準。實測結果：

| 項目 | 免費額度 | 本站用量 | 月費 |
| --- | --- | --- | --- |
| Cloud Run | 180,000 vCPU-秒、360,000 GiB-秒、2M 請求 | 遠低於額度 | NT$0 |
| Neon PostgreSQL | 0.5 GB、100 compute-hours | < 1 MB | NT$0 |
| Artifact Registry | **0.5 GB（帳單帳戶共用）** | 約 128 MB | NT$0 |
| Cloud Storage | 5 GB（**僅限美國區域**） | 37 KB（asia-east1） | ~NT$0.1 |
| Secret Manager | 6 個 active 版本 | 3 個 | NT$0 |
| Cloud Build | 2,500 建置分鐘 | 每次約 1.5 分鐘 | NT$0 |
| **合計** | | | **≈ NT$0** |

**維持零成本的三個關鍵**：

1. **`--min-instances=0`** —— 調成 1 會從 NT$0 變成每月數百元。
2. **不要建立 Cloud SQL** —— 那是持續計費，零流量也照收（約 NT$250–320/月）。
3. **定期清理 Artifact Registry** —— 免費額度是整個帳單帳戶共用，
   舊映像會累積。清理前先確認沒有 Cloud Run Job 引用（見 §1.1）。

> Cloud Storage 的 5 GB 免費額度**只適用美國區域**
> （`us-east1`／`us-west1`／`us-central1`）。本案 bucket 放在 `asia-east1`
> 以貼近使用者，因此不在免費範圍 —— 但數十 MB 的媒體每月成本不到 NT$1，
> 為了延遲而付這筆錢是划算的取捨。

**務必設定預算警示**：

```bash
gcloud billing budgets create --billing-account=<ACCOUNT_ID> \
  --display-name="NTUST SiPh Lab - TWD 50 Alert" \
  --budget-amount=50TWD \
  --threshold-rule=percent=0.5 \
  --threshold-rule=percent=0.9 \
  --threshold-rule=percent=1.0 \
  --filter-projects="projects/${PROJECT_ID}"
```

---

## 相關文件

- **`docs/HANDOFF.md`** — **已上線站台的實測盤點、維運指令與踩過的坑**
- `docs/adr/ADR-013` — 以 Neon 取代 Cloud SQL 的決策與代價
- `docs/adr/ADR-014` — `/healthz` 為保留路徑、`/health` 別名
- `deploy/migration-runbook.md` — 完整 cutover 與 rollback 程序
- `deploy/service.yaml` — 宣告式 Cloud Run 設定（選用）
- `docs/cloudrun-deployment.md` — 環境變數逐項說明
