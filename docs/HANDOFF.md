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
| **後台表單修復（`fix/admin-form-integrity`）** | 🟡 **已完成待部署** — 見 §10.1 |

> **線上跑的仍是舊版**：後台的儲存鈕在部分頁面（含教授頁）因巢狀 `<form>`
> 而失效，該修復尚未部署。詳見 §10.1。

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

## 4. Git 分支現況

**兩個功能分支已於 2026-08-17 全部合併進 `main`**（以 `--no-ff` 保留分支歷史）：

```
main  69fba38
 ├── Merge: 中英雙語切換（feat/bilingual-toggle）           4 個 commit
 └── Merge: Cloud Run 部署與文件（feat/cloudrun-deployment）  7 個 commit
```

**目前只有 `main` 一個分支**（本機與遠端皆是）。
兩個功能分支合併後已刪除 —— 它們已落後 `main` 7～10 個 commit，
留著只會讓人誤以為可以直接 checkout 繼續開發，
而那些過期分支上沒有 `/health`、ADR，`.gitignore` 也缺少
`migration-package/` 的保護規則。

刪除不會遺失任何東西：所有 commit 都是 `main` 的祖先，
`git log --graph` 仍看得出分支結構（因為合併時用了 `--no-ff`），
合併 commit 的訊息也記錄了原始分支名稱。
需要時可用 `git branch <名稱> <SHA>` 隨時重建。

兩個分支**零檔案重疊**（已逐檔驗證），合併無衝突。

> **合併正確性的驗證方式**：合併後 `pytest` 為 **818 passed**，
> 正是 i18n 分支的 815 加上部署分支新增的 3 個 health 測試。
> 數字相符即證明合併結果是正確的聯集，沒有任何一邊被覆蓋。

分離時用過的 `backup/bilingual-toggle-before-split` 已在確認所有內容
都存在於遠端後刪除。

> **`main` 現在等於線上跑的程式碼。**
> 線上 revision `ntust-siph-lab-00002-dab` 的映像 `c146cd8-health-alias`
> 建置於這兩個分支的聯集，與合併後的 `main` 內容一致。
> 因此從 `main` 重新建置不會改變線上行為。

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

✅ **此風險已於 2026-08-17 隨部署分支合併進 `main` 而解除** ——
`.gitignore` 的排除規則現在存在於 `main`，任何從 `main` 開出的分支
都會繼承。

> 歷史紀錄：這條規則原本只在 `feat/cloudrun-deployment` 上，
> 在 `main` 執行匯出仍有誤 commit 管理員密碼雜湊的風險。
> 若日後有人從合併前的舊 commit 開分支，請確認該規則存在。

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
| 修正 §7.3 的連線字串正規化 | 讓遷移腳本接受標準 `postgresql://` |
| 簽核後開放索引 | `ROBOTS_POLICY=public`（指令見 §6） |
| 關閉舊 Google Sites | **等簽核完成**，依 `deploy/migration-runbook.md` |
| 自訂網域 | 校方 DNS 就緒後設定，並同步改 `PUBLIC_BASE_URL` |

### 8.3 商務文件（刻意不在版控中）

`scripts/generate_valuation_pdf.py` 產生的是「系統價值評估與請款對應報告」，
檔案本身標示「機密與內部參考文件」，產出的 PDF 位於 repo 之外的上層資料夾。

它雖然主題是本專案，但屬於商務範疇而非網站系統。這個 repo 未來可能交付校方，
報價與請款金額不應出現在其中，因此已加入 `.gitignore`
（連同 `*價值評估*.pdf`、`*請款*.pdf`）。

**該檔案仍留在本機工作區**，只是不會進版控。要不要移到 repo 之外由委託人決定。

---

## 9. 文件索引

| 文件 | 內容 |
| --- | --- |
| `docs/SAI.md` | 系統規格書（外部交付，**不應改寫**）。決策表含 ADR-001～011 |
| **`docs/NOTES.md`** | **碼層決策紀錄（NOTE-001～009）。程式中的 `NOTE(NOTE-NNN):` 都指向這裡** |
| `docs/adr/ADR-012` | 母站遷入人物的發布豁免 |
| **`docs/adr/ADR-013`** | **以 Neon 取代 Cloud SQL（本次）** |
| **`docs/adr/ADR-014`** | **`/healthz` 保留路徑與 `/health` 別名（本次）** |
| `deploy/cloudrun.md` | 部署步驟（**已改寫為 Neon 免費架構**，含 §11 成本章節） |
| `deploy/migration-runbook.md` | cutover 與 rollback 程序（已移除 Cloud SQL Proxy） |
| `deploy/service.yaml` | 宣告式設定（**已改寫**，3 個 secret、無 cloudsql 註解） |
| `docs/acceptance-checklist.md` | AC-01～AC-26 驗收清單 |
| `docs/header-note-audit-2026-08-18.md` | 檔頭與 NOTE 制度稽核（含尚未完成的補件清單） |
| `legacy/google_sites/` | 母站零遺漏遷移的稽核證據鏈（ADR-011） |

> **三層決策文件不得互相複製**：`SAI.md`（ADR-001～011，外部規格，不改寫）
> → `docs/adr/`（ADR-012 起的新決策）→ `docs/NOTES.md`（碼層不變量）。
> `tests/test_repo_integrity.py` 會檢查編號都查得到對應條目，
> 以及檔頭 `驗證方式` 指到的測試函式真的存在。

> `deploy/` 底下三份文件已於 2026-08-17 全面對齊實際部署。
> 文件中仍出現「Cloud SQL」字樣之處，都是在說明**為什麼不用它**，
> 不是操作指示。

---

## 10. 驗收紀錄（2026-08-17）

> 本節是 **2026-08-17 首次 cutover** 的紀錄。
> 後續變更的驗收另記於 §10.1。

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

---

## 10.1 驗收紀錄（2026-09-02，`fix/admin-form-integrity`）

修復後台表單的一系列缺陷（巢狀 `<form>` 導致儲存鈕失效、多值列遺失、
無二次確認、無連點防護等），以及四項「出錯後管理者走不出來」的問題。

| Gate | 結果 |
| --- | --- |
| G1 本機測試 | ✅ **914 passed, 3 skipped** |
| G2 PostgreSQL 可攜性（靜態掃描） | ✅ 23/23 |
| G2 PostgreSQL 可攜性（實際連線） | ⚠️ **未重跑** — 理由見下 |
| Schema 變更 | ✅ **無**。revision 仍為 `16bde59ce22f` |
| 合併方式 | ✅ fast-forward，零衝突 |
| 教授頁存檔（瀏覽器實測） | ✅ 儲存成功，PRG 與 flash 正常 |
| 連點防護（瀏覽器實測） | ✅ 點 5 次只送出 1 次 |
| 送出鎖解除（瀏覽器實測） | ✅ bfcache 返回後可再次送出 |
| 中英切換 | ✅ `htmlLang` 正確，`about_intro_en` 生效 |

### 為什麼這次沒有重跑 G2 的實際連線測試

**不是因為它不重要，是因為這次的變更不觸及它所保護的東西。**

1. **零 schema 變更。** 本分支對 `migrations/` 只改了檔頭註解，
   Alembic revision 與 2026-08-17 通過 G2 時完全相同（`16bde59ce22f`）。
   同一份 schema 已經在該次 gate 通過，也已在正式環境實際運行。
2. **變更範圍不含資料庫層。** 改動集中在 `app/static/js/admin.js`、
   admin 模板、`routes.py` 的表單渲染流程與 `forms.py` 的表單解析。
   沒有新增查詢、沒有改動 ORM 模型、沒有新的 raw SQL。
3. **靜態可攜性契約仍全數驗證。** `test_db_portability.py` 的 25 個測試
   有 23 個不需要 PostgreSQL 連線，且全部通過 —— 包含「不得有 raw SQL」、
   「不得用 PRAGMA」、「不得用 `strftime` / `julianday` / `group_concat`」、
   「migration 不得使用 native enum」等真正會造成 PG 不相容的檢查。

### ⛔ 下次必須重跑的條件

**只要 `migrations/versions/` 新增任何檔案，G2 就不得再跳過。**
屆時依 §7.4 的指令對 `siph_test` 執行（**不是 `neondb`**）。

### 本次的操作警訊（保留為紀錄）

協作過程中曾提供一組指向名為 `neondb` 之資料庫的連線字串作為「測試連線」。
經唯讀檢查確認該端點的 schema 為空（0 張表），屬於另一個 Neon 專案，
因此未造成任何損害。

但 `neondb` 正是本專案**正式資料庫的名稱**（見 §2.2），而 G2 測試會執行
`DROP SCHEMA IF EXISTS public CASCADE`。**擋下這次的是事前的唯讀檢查，
不是流程本身。**

→ 執行 G2 之前，一律先確認目標資料庫名稱是 `siph_test`，
並以 `SELECT COUNT(*) FROM people` 之類的唯讀查詢確認它不含正式資料。
