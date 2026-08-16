# ADR-013 — 以 Neon 免費方案取代 Cloud SQL 作為正式環境 PostgreSQL

> **檔案路徑**：`docs/adr/ADR-013-managed-postgres-provider.md`
> **建立日期**：2026-08-17　**版本**：v1.0
> **狀態**：**鎖定（Accepted）**

---

## 為什麼這份 ADR 存在

`deploy/cloudrun.md` §1.2 對 Cloud SQL 的成本提醒明文規定：

> 成本提醒（SAI §24）：Cloud SQL 是**持續計費**的資源。請在建立後
> 立刻設定 budget alert。若成本不可接受，必須另開 ADR 評估其他
> managed PostgreSQL，**不得**私自改回 SQLite 或變更資料模型。

本檔即為該條款要求的載體。委託人要求全站以零成本上線，
Cloud SQL 的持續計費與該要求直接衝突，因此觸發此條款。

---

## 1. 背景：ADR-005 的前提在本案不成立

| 決策 | 原始要求 |
| --- | --- |
| ADR-005 | 正式環境使用 **Cloud SQL for PostgreSQL** |
| ADR-004 | 正式環境**禁止**以 SQLite 作為永久資料庫 |
| ADR-006 | 媒體一律進 Cloud Storage |

ADR-005 選擇 Cloud SQL 的前提是「專案願意負擔 managed database 的月費」。
本次部署的委託條件是**零成本**，該前提不成立。

實測成本（2026-08-17 查證）：

| 方案 | 月費 | 是否符合零成本要求 |
| --- | --- | --- |
| Cloud SQL `db-f1-micro`（asia-east1，10GB SSD，無 HA） | 約 US$7–10（NT$250–320） | ❌ |
| Neon 免費方案 | US$0 | ✅ |

Cloud SQL **沒有免費方案**，且為持續計費——即使網站零流量仍照收。
這與 Cloud Run 的 scale-to-zero 特性相反，會成為整體架構中唯一的固定支出。

---

## 2. 裁示

| 項目 | 內容 |
| --- | --- |
| **決定** | 正式環境改用 **Neon 免費方案**（managed PostgreSQL），不建立 Cloud SQL 執行個體 |
| 核准人 | 研究室管理者（本專案委託人） |
| 核准日期 | **2026-08-17** |
| 核准依據 | 委託人於 2026-08-17 的部署委託中指定方案 |
| **委託人原話** | 「我要把他架上去免費gcp搭配免費cloud run」；於資料庫選項中選擇「Neon 免費方案」 |

> **重要**：以上「委託人原話」為委託人本人陳述，未經審查者潤飾。
> 以下第 3、4、5 節為**審查者（實作者）補充的工程分析**，
> 不得混入核准紀錄冒充委託人意見（ADR-011 核心要求）。

---

## 3. 為什麼 Neon 而非其他免費 PostgreSQL（審查者補充）

| 供應商 | 免費額度 | 否決理由 |
| --- | --- | --- |
| **Neon** | 0.5 GB／專案、100 compute-hours／月、閒置自動縮到零 | — **採用** |
| Supabase | 500 MB DB | **無流量滿 7 天會暫停專案**。研究室網站寒暑假可能低流量，暫停會造成非預期停機 |
| Cloud SQL | 無免費方案 | 不符零成本要求 |

本站資料量為 **SQLite 172 KB**，媒體另存 GCS 不佔 DB，
0.5 GB 額度有數量級的餘裕。

---

## 4. 這個決定「沒有」改變什麼（審查者補充）

這是本 ADR 最重要的邊界宣告——**替換的只有供應商，不是架構**：

| 項目 | 是否改變 |
| --- | --- |
| 資料庫引擎仍為 PostgreSQL | ❌ 不變（ADR-004 的「禁止 SQLite」仍完全成立） |
| 資料模型與 7 張核心表 | ❌ 不變 |
| Alembic migration | ❌ 不變（同一套 migration 直接套用） |
| `ProductionConfig` fail-fast 檢查 | ❌ 不變（仍拒絕 SQLite 與非 GCS 儲存） |
| ADR-006 媒體進 GCS | ❌ 不變 |
| **應用程式碼** | ❌ **一行未改** |

`app/config.py` 的 `_normalize_database_url()` 會把 Neon 提供的
`postgresql://` 連線字串自動轉為 `postgresql+psycopg://`，
與 Cloud SQL 走的是同一條路徑。

部署層唯一差異：`gcloud run deploy` **不再需要** `--add-cloudsql-instances`，
`DATABASE_URL` 改由 Secret Manager 以完整連線字串注入
（Cloud SQL 版本是 unix socket + 密碼分離注入）。

---

## 5. 後果與已知代價（審查者補充）

**正面**

- 整體架構月費由約 NT$250–320 降為 **≈ NT$0**。
- 資料庫與 Cloud Run 同樣具備 scale-to-zero，無閒置成本。

**代價（已知並接受）**

1. **跨雲延遲**：Neon 無台灣區域，最近為東京／新加坡。
   Cloud Run（asia-east1）到 Neon 的每次查詢往返約增加 30–60 ms。
   本站為 SSR、每頁查詢數少，影響可接受；但**不適合**未來加入
   高查詢密度的功能（例如逐字搜尋 autocomplete）。
2. **冷啟動疊加**：Neon 閒置 5 分鐘後計算節點縮到零，
   首次查詢需喚醒（約 0.5–1 秒），會與 Cloud Run 冷啟動疊加。
   低流量時段的第一位訪客會感受到較慢的首頁。
3. **資料離開 GCP**：資料庫不再位於 GCP 專案邊界內。
   本站公開內容不含個資以外的敏感資料，但
   **`audit_logs` 與 admin 密碼雜湊會存於 Neon**。
   連線一律走 TLS（`sslmode=require`）。
4. **供應商依賴**：免費方案條款可能變動。
   若 Neon 政策改變，遷移路徑為「匯出 → 匯入 Cloud SQL」，
   因資料模型未變，`scripts/import_postgres.py` 可直接重用。

---

## 6. 後續責任

- **G2 Gate 仍須執行**：`TEST_POSTGRES_URL=<neon-url> pytest -m postgres`
  必須全綠，才算通過 SAI §21.1 的 DB portability gate。
  這道 gate 不因換供應商而豁免。
- 於 GCP 專案 `ntust-siph-lab` 設定 budget alert，
  監控 Cloud Run／Artifact Registry／GCS 的殘餘費用。
- 若日後導入 Cloud SQL，本 ADR 應標記為 Superseded 而非刪除。

---

## 7. 相關

- 被本檔取代的決策：**ADR-005**（Cloud SQL for PostgreSQL）— 於本案範圍內
- 仍然成立的上位決策：ADR-004（禁止 SQLite 作為正式資料庫）、ADR-006（媒體進 GCS）
- 觸發條款：`deploy/cloudrun.md` §1.2 成本提醒
- 實作：`deploy/cloudrun.md`、`deploy/service.yaml`、`app/config.py`（未修改）
- 驗證：`tests/test_db_portability.py`、`tests/test_migration_roundtrip.py`
