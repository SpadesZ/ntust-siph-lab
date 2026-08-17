# NTUST SiPh Lab — 上線交接文件（HANDOFF）

> **檔案路徑**：`docs/HANDOFF.md`
> **建立日期**：2026-08-17　**版本**：v1.0
> **對應部署**：Cloud Run revision `ntust-siph-lab-00002-dab`
>
> 這份文件寫給「下一個接手的人或 AI」。目標是讓你**不必重讀整段對話**
> 就能維護這個站台。所有數字都是實測值，不是規劃值。

---

## 0. 一句話現況

**網站已上線並可公開瀏覽，每月費用近乎 NT$0；但「內容簽核」尚未完成，
因此刻意設為不被搜尋引擎索引，舊 Google Sites 也刻意未關閉。**

| 項目 | 狀態 |
| --- | --- |
| 線上服務 | ✅ 運作中 |
| 資料遷移 | ✅ 完成並驗證 |
| SAI §21.1 G1–G6 自動檢查 | ✅ 全過 |
| **內容簽核（人工）** | ❌ **未完成 — 這是唯一的上線阻擋項** |
| 搜尋引擎索引 | ⛔ 刻意關閉（`ROBOTS_POLICY=private`） |
| 舊站 Google Sites | 🟢 仍在線（刻意保留，等簽核） |
| 部署分支合併進 main | ❌ 尚未合併 |

---

## 1. 線上位址

| 用途 | 網址 |
| --- | --- |
| **正式站** | https://ntust-siph-lab-105924420674.asia-east1.run.app |
| 舊格式網址（同一個服務，也可用） | https://ntust-siph-lab-zuwnb72d7q-de.a.run.app |
| 管理後台 | `/admin/login` |
| 健康檢查（外部監控用） | `/health` ← **不是 `/healthz`，原因見 §7.1** |
| 舊站（尚未關閉） | https://sites.google.com/view/ntust-siph-lab/ |

`PUBLIC_BASE_URL` 設為第一個網址，canonical 與 sitemap 都指向它。
**若日後換自訂網域，必須同步改 `PUBLIC_BASE_URL`**，否則 SEO 會靜默指向錯誤位置。

### 帳號密碼放在哪

**本文件不含任何密碼。**

| 憑證 | 存放位置 |
| --- | --- |
| `SECRET_KEY` | GCP Secret Manager → `siph-secret-key` |
| `AUDIT_IP_SALT` | GCP Secret Manager → `siph-audit-ip-salt` |
| 資料庫連線字串（含密碼） | GCP Secret Manager → `siph-database-url` |
| 管理員密碼 | **未存放於任何系統**，2026-08-17 重設後交付管理者本人 |

取用（需 GCP 權限）：

```bash
gcloud secrets versions access latest --secret=siph-database-url --project=ntust-siph-lab
```

管理員帳號為 `admin`。密碼是 scrypt 單向雜湊，**無法還原**。
忘記時只能重設，不能查詢：

```bash
export DATABASE_URL="$(gcloud secrets versions access latest --secret=siph-database-url --project=ntust-siph-lab)"
export APP_ENV=local FLASK_APP=wsgi.py
flask admin reset-password --username admin --generate
```

---

## 2. 基礎設施盤點（2026-08-17 實測）

### 2.1 GCP

| 項目 | 值 |
| --- | --- |
| 專案 ID / 編號 | `ntust-siph-lab` / `105924420674` |
| 帳單帳戶 | `0128A1-E01F1C-20610E`（TWD） |
| 區域 | `asia-east1` |
| 預算警示 | `NTUST SiPh Lab - TWD 50 Alert`（50/90/100%） |

**Cloud Run 服務 `ntust-siph-lab`**

| 設定 | 值 |
| --- | --- |
| 目前 revision | `ntust-siph-lab-00002-dab` |
| 映像 | `asia-east1-docker.pkg.dev/ntust-siph-lab/siph-lab/ntust-siph-lab:c146cd8-health-alias` |
| CPU / 記憶體 | 1 / 512Mi |
| minScale / maxScale | **0** / 4 |
| 並行數 / 逾時 | 40 / 60s |
| startup CPU boost | 開啟 |
| 服務帳號 | `siph-lab-run@ntust-siph-lab.iam.gserviceaccount.com` |

> `minScale=0` 是零成本的關鍵：沒有流量時不計費。**不要為了避免冷啟動而調成 1**，
> 那會讓費用從 NT$0 變成每月數百元。

**環境變數（明文）**

```
APP_ENV=production                    STORAGE_BACKEND=gcs
DB_BACKEND=postgresql                 GCS_BUCKET=ntust-siph-lab-siph-media
SESSION_COOKIE_SECURE=true            PERMANENT_SESSION_LIFETIME_HOURS=8
LOG_LEVEL=INFO                        ROBOTS_POLICY=private   ← 簽核後改 public
ENABLE_LLMS_TXT=false                 WEB_CONCURRENCY=2
DB_POOL_SIZE=5                        DB_MAX_OVERFLOW=2
PUBLIC_BASE_URL=https://ntust-siph-lab-105924420674.asia-east1.run.app
```

**環境變數（Secret Manager 注入）**：`SECRET_KEY`、`AUDIT_IP_SALT`、`DATABASE_URL`

**其他資源**

| 資源 | 值 |
| --- | --- |
| Artifact Registry | `asia-east1-docker.pkg.dev/ntust-siph-lab/siph-lab`（127 MB，2 個映像） |
| 媒體 bucket | `gs://ntust-siph-lab-siph-media`（asia-east1，37 KB，**公開讀取**） |
| Cloud Build 暫存 bucket | `gs://ntust-siph-lab_cloudbuild`（US，3.5 MB） |
| 已啟用 API | run / artifactregistry / storage / secretmanager / cloudbuild |
| **未**啟用 | `sqladmin` — 刻意不啟用，本案不使用 Cloud SQL（見 ADR-013） |

> 媒體 bucket 對 `allUsers` 開放 `objectViewer`。這是刻意的：
> `app/storage/gcs.py` 的 `public_url()` 產生 `storage.googleapis.com` 直連網址，
> bucket 若改為私有，前台圖片會全部 403。詳見 `deploy/cloudrun.md` §1.3。

### 2.2 資料庫（Neon PostgreSQL，非 Cloud SQL）

| 項目 | 值 |
| --- | --- |
| 供應商 | Neon 免費方案（**不是 Cloud SQL**，決策見 ADR-013） |
| 版本 | PostgreSQL 18.4 |
| 區域 | AWS `ap-southeast-1`（新加坡） |
| 正式資料庫 | `neondb` |
| 測試資料庫 | `siph_test` ← G2 測試專用，見 §7.4 |
| Alembic revision | `16bde59ce22f`（head） |
| 連線端點 | **direct（非 pooler）**，原因見 §7.2 |

**資料量**

| 資料表 | 筆數 |
| --- | --- |
| `people` | 5（faculty 1、current 4） |
| `research_outputs` | 9 |
| `research_output_people` | 9 |
| `admin_users` | 1 |
| `site_settings` | 1 |
| `audit_logs` | 93 |
| `redirects` | 0 |

媒體：1 個檔案（`people/67f1d4a69b5844b1842adf80c9862413.jpg`，37 KB）

---

## 3. 每月費用（實測）

| 項目 | 用量 | 月費 |
| --- | --- | --- |
| Cloud Run | minScale=0，低流量 | **NT$0**（免費額度內） |
| Neon PostgreSQL | 資料 < 1 MB / 0.5 GB | **NT$0** |
| Artifact Registry | 399 MB / 500 MB 免費額度（含另一專案） | **NT$0** |
| Cloud Storage | 3.6 MB | ~NT$0.1 |
| Secret Manager / Cloud Build | 免費額度內 | NT$0 |
| **合計** | | **≈ NT$0 / 月** |

> Artifact Registry 的 0.5 GB 免費額度是**整個帳單帳戶共用**的，
> 不是每個專案各有 0.5 GB。目前另一個專案 `best-math-leave` 佔 272 MB。
> 累積映像時要留意總量，超出部分約 US$0.10/GB/月。

原規劃的 Cloud SQL 方案約 NT$250–320/月，改用 Neon 後省下這筆固定支出。

---

## 4. Git 分支現況 ⚠️

```
origin/main  ec995f1
  ├── feat/bilingual-toggle    c146cd8  (本地，4 個 i18n commit，未推送)
  └── feat/cloudrun-deployment c8ec32e  (已推送，3 個部署 commit)
```

| 分支 | 內容 | 狀態 |
| --- | --- | --- |
| `feat/cloudrun-deployment` | 部署相關（本次） | ✅ 已推送，**尚未合併** |
| `feat/bilingual-toggle` | 中英切換功能 | ⚠️ 僅存在本地，**尚未推送** |
| `backup/bilingual-toggle-before-split` | 分離前的備份 | 可在確認無誤後刪除 |

**兩個分支零檔案重疊**，可獨立審查與合併。

> **目前線上跑的程式碼 = `c146cd8`（i18n）+ 3 個部署 commit**，
> 也就是兩個分支的聯集。映像 tag `c146cd8-health-alias` 反映這件事。
> 若只合併其中一個分支就重新建置，線上行為會改變 —— 重新部署前請先確認
> 你要的是哪個組合。

---

## 5. 如何重新部署

```bash
cd "C:\Users\Franky Kuo\Desktop\NTUST SiPh Lab WEB\ntust-siph-lab"

# 1) 本機測試必須全綠
.\.venv\Scripts\python.exe -m pytest -q

# 2) 建置（tag 用 git SHA；工作區若有未提交變更，請加後綴標明）
$SHA = git rev-parse --short HEAD
$IMG = "asia-east1-docker.pkg.dev/ntust-siph-lab/siph-lab/ntust-siph-lab:$SHA"
gcloud builds submit --tag $IMG --project=ntust-siph-lab --region=asia-east1

# 3) 先不切流量，部署到 candidate 標籤
gcloud run deploy ntust-siph-lab --image=$IMG --region=asia-east1 `
  --no-traffic --tag=candidate --project=ntust-siph-lab

# 4) 對 candidate 網址跑 smoke test（網址會由上一步印出）
.\.venv\Scripts\python.exe scripts\smoke_cloud.py "https://candidate---ntust-siph-lab-zuwnb72d7q-de.a.run.app"

# 5) 通過後才切流量
gcloud run services update-traffic ntust-siph-lab --region=asia-east1 `
  --to-latest --project=ntust-siph-lab

# 6) 對正式網址再跑一次
.\.venv\Scripts\python.exe scripts\smoke_cloud.py "https://ntust-siph-lab-105924420674.asia-east1.run.app"
```

環境變數與 secret 綁定會沿用，只換映像時不需要重新指定。

### 回滾

```bash
gcloud run services update-traffic ntust-siph-lab --region=asia-east1 `
  --to-revisions=ntust-siph-lab-00001-dx5=100 --project=ntust-siph-lab
```

---

## 6. 常見維運操作

**開放搜尋引擎索引（內容簽核完成後）** — 不需重建映像：

```bash
gcloud run services update ntust-siph-lab --region=asia-east1 `
  --update-env-vars=ROBOTS_POLICY=public --project=ntust-siph-lab
```

**查看日誌**：

```bash
gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="ntust-siph-lab"' `
  --project=ntust-siph-lab --limit=50 --freshness=1h
```

**輪替資料庫密碼**：在 Neon 儀表板 Reset password → 建立新的 secret 版本 → 重新部署：

```bash
gcloud secrets versions add siph-database-url --data-file=<檔案> --project=ntust-siph-lab
gcloud run services update ntust-siph-lab --region=asia-east1 --project=ntust-siph-lab
```

**連線資料庫**（psycopg 需要去掉 SQLAlchemy 的 `+psycopg`）：

```bash
URL="$(gcloud secrets versions access latest --secret=siph-database-url --project=ntust-siph-lab)"
psql "${URL/+psycopg/}"
```

---

## 7. 踩過的坑（**接手前務必讀**）

以下每一項都是實際踩到、且**本機開發與 CI 完全看不出來**的問題。

### 7.1 `/healthz` 被 Cloud Run 保留

從公開網址打 `/healthz` 永遠回 Google 的 404，請求不會進到容器，
Cloud Run 的 request log 也不會有紀錄。

- **外部監控一律用 `/health`**
- 容器內的 probe 與 Dockerfile `HEALTHCHECK` 仍用 `/healthz`，那是正常的
- 鑑別方法與完整說明：**ADR-014**

### 7.2 Neon 要用 direct 端點，不要用 pooler

Neon 的 `-pooler` 端點是 PgBouncer transaction 模式，
而 psycopg3 會自動建立 prepared statement，兩者相衝會產生
`prepared statement already exists`。

本案 maxScale=4 × pool 7 = 最多 28 連線，遠低於上限，**用直連最單純**。
`siph-database-url` 存的就是直連端點（主機名稱**不含** `-pooler`）。

### 7.3 遷移腳本不會正規化連線字串

`scripts/import_postgres.py` 與 `tests/test_db_portability.py` 會把
連線字串**直接**交給 SQLAlchemy，不經過 `app.config._normalize_database_url()`。

Neon／Supabase／Heroku 給的是 `postgresql://` 開頭，SQLAlchemy 會據此
選擇 psycopg2 驅動，而本專案裝的是 psycopg3，於是報出誤導性的：

```
ModuleNotFoundError: No module named 'psycopg2'
```

**解法**：傳給這些腳本時，手動改成 `postgresql+psycopg://` 開頭。
（應用程式本身沒有這個問題，`ProductionConfig` 會正規化。）
這是已知待修項，見 §8。

### 7.4 G2 測試會清空目標資料庫

`tests/test_db_portability.py` 會執行 `DROP SCHEMA IF EXISTS public CASCADE`。

**絕對不要把 `TEST_POSTGRES_URL` 指向 `neondb`**，那會刪光正式資料。
已為此建立獨立的 `siph_test` 資料庫：

```bash
$URL = gcloud secrets versions access latest --secret=siph-database-url --project=ntust-siph-lab
$env:TEST_POSTGRES_URL = $URL -replace '/neondb\?', '/siph_test?'
.\.venv\Scripts\python.exe -m pytest -m postgres
```

### 7.5 `migration-package/` 含管理員密碼雜湊

`scripts/export_sqlite.py` 的產出包含 `tables/admin_users.json`（密碼雜湊）
與 `audit_logs.json`。

⚠️ **這個目錄只有在 `feat/cloudrun-deployment` 分支上才被 `.gitignore` 排除。**
在 `main` 或 `feat/bilingual-toggle` 上執行匯出，**有可能誤 commit 進版控**。
合併部署分支後此風險才會消失。

### 7.6 smoke test 有兩種「預期中的失敗」

| 失敗項目 | 何時出現 | 是否需要處理 |
| --- | --- | --- |
| `robots.txt 禁止 /admin` | `ROBOTS_POLICY=private` 時 | ❌ 不需要。private 輸出全站 `Disallow: /`，涵蓋範圍更嚴格。改成 public 後會自動通過 |
| `canonical/sitemap 指向本站網域` | 測試 `candidate---` 標籤網址時 | ❌ 不需要。canonical 指向正式網址才是對的 |

目前對正式網址的成績是 **24/25**，開放索引後會是 25/25。

### 7.7 用腳本測登入要帶 `Referer`

Flask-WTF 對 HTTPS 的 POST 會做 strict referer 檢查。
腳本若不帶 `Referer` 標頭會拿到 **HTTP 400**，看起來像密碼錯誤，其實不是。
瀏覽器正常操作不會遇到。

---

## 8. 未完成事項

### 8.1 ⛔ 內容簽核（唯一的上線阻擋項，**只有教授能做**）

`legacy/google_sites/content_signoff.md`：

- **§2.4** — 首頁與 `/about` 導言文案待核准（5 則文字）
- **§5** — 6 項確認未勾選、12 個簽核欄位空白，包含：
  - 教授姓名、職稱、學歷、六項專長、Email 與外部連結是否完全正確
  - 四位在學成員姓名是否無誤
  - 新站是否有憑空產生的研究成果或畢業生資料

> **任何人（含 AI）都不得代填這些欄位。** 那等同偽造核准紀錄，
> 是 ADR-011 與 ADR-012 明文禁止的行為。

`scripts/verify_migration.py --all` 的自動檢查已全數通過（UNRESOLVED = 0），
但仍會以 exit code 2 結束，正是因為這道人工關卡。

### 8.2 待處理清單

| 項目 | 說明 |
| --- | --- |
| 輪替資料庫密碼 | Neon 連線字串曾顯示於協作對話中，建議重設 |
| 輪替管理員密碼 | 2026-08-17 重設後的密碼曾顯示於對話中，建議管理者登入後自行更改 |
| 合併 `feat/cloudrun-deployment` | 合併後 §7.5 的風險才消失 |
| 推送 `feat/bilingual-toggle` | 目前只在本地，**有遺失風險** |
| 修正 §7.3 的連線字串正規化 | 讓遷移腳本接受標準 `postgresql://` |
| 簽核後開放索引 | `ROBOTS_POLICY=public`（指令見 §6） |
| 關閉舊 Google Sites | **等簽核完成**，依 `deploy/migration-runbook.md` |
| 自訂網域 | 校方 DNS 就緒後設定，並同步改 `PUBLIC_BASE_URL` |
| 刪除備份分支 | 確認無誤後 `git branch -D backup/bilingual-toggle-before-split` |

### 8.3 與本站無關的檔案

工作區有一個未追蹤檔案 `scripts/generate_valuation_pdf.py`（2026-08-17 12:03 建立），
**不是本次部署產生的**，用途不明，未納入任何 commit。接手時請向委託人確認去留。

---

## 9. 文件索引

| 文件 | 內容 |
| --- | --- |
| `docs/SAI.md` | 系統規格書（外部交付，**不應改寫**） |
| `docs/adr/ADR-012` | 母站遷入人物的發布豁免 |
| **`docs/adr/ADR-013`** | **以 Neon 取代 Cloud SQL（本次）** |
| **`docs/adr/ADR-014`** | **`/healthz` 保留路徑與 `/health` 別名（本次）** |
| `deploy/cloudrun.md` | 部署步驟（§1.2 仍描述 Cloud SQL，已被 ADR-013 取代） |
| `deploy/migration-runbook.md` | cutover 與 rollback 程序 |
| `deploy/service.yaml` | 宣告式設定（**仍含 Cloud SQL 註解，未套用於本次部署**） |
| `docs/acceptance-checklist.md` | AC-01～AC-26 驗收清單 |
| `legacy/google_sites/` | 母站零遺漏遷移的稽核證據鏈（ADR-011） |

> ⚠️ `deploy/cloudrun.md` §1.2／§6 與 `deploy/service.yaml` 仍描述 Cloud SQL 的做法。
> **實際部署未使用 Cloud SQL**（ADR-013）。這兩份文件尚未全面改寫，
> 閱讀時請以 ADR-013 與本文件 §5 為準。

---

## 10. 驗收紀錄（2026-08-17）

| Gate | 結果 |
| --- | --- |
| G1 本機測試 | ✅ 818 passed, 3 skipped |
| G2 PostgreSQL 可攜性 | ✅ 3/3（對 `siph_test`） |
| G3 資料匯出 | ✅ 7 表 114 筆，checksum 齊全 |
| G4 媒體同步 | ✅ 公開 URL 可讀，sha256 端到端一致 |
| G5 回滾點 | ✅ `backups/siph-lab-backup-20260816T164921Z.zip` |
| G6 自動複驗 | ✅ AC-21～AC-25 全過，UNRESOLVED = 0 |
| G6 人工簽核 | ❌ **未完成**（見 §8.1） |
| Smoke test | ✅ 24/25（第 25 項見 §7.6） |
| 端到端資料驗證 | ✅ 5 位成員與 9 筆成果正確渲染於線上頁面 |
| 管理後台登入 | ✅ 實測登入成功並進入 Dashboard |
