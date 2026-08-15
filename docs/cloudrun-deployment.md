# Cloud Run 環境變數與設定參考

> 對應 SAI §21 與附錄 B。
> **操作步驟**在 `deploy/cloudrun.md`；**cutover 程序**在
> `deploy/migration-runbook.md`。本文只逐項說明「設定」。

---

## 環境變數完整清單

### 必要（缺少會導致啟動失敗）

| 變數 | 範例 | 說明 |
| --- | --- | --- |
| `APP_ENV` | `production` | **必須精確為 `production`**，否則不會套用 ProductionConfig 的 fail-fast 檢查 |
| `SECRET_KEY` | Secret Manager | session 與 CSRF 簽章金鑰。高熵，禁止 commit |
| `DATABASE_URL` | `postgresql+psycopg://...` | 禁止 SQLite（ADR-004） |
| `PUBLIC_BASE_URL` | `https://siph-lab.ntust.edu.tw` | canonical／sitemap／OG 的根。**不得含 localhost** |
| `STORAGE_BACKEND` | `gcs` | 必須是 `gcs`（ADR-006） |
| `GCS_BUCKET` | `<project>-siph-media` | 媒體 bucket |

`ProductionConfig.init_app()` 會檢查以上條件，不符即 `RuntimeError`。
這是刻意的 fail-fast：Cloud Run revision 部署失敗會保留舊 revision
繼續服務，比用不安全的設定成功上線好。

### 建議設定

| 變數 | 預設 | 建議值 | 說明 |
| --- | --- | --- | --- |
| `SESSION_COOKIE_SECURE` | `true` | `true` | 僅 HTTPS 傳送 cookie |
| `PERMANENT_SESSION_LIFETIME_HOURS` | `8` | `8` | session 絕對有效期 |
| `LOGIN_RATE_LIMIT` | `5 per 15 minutes` | 同左 | 登入頻率限制 |
| `LOG_LEVEL` | `INFO` | `INFO` | 日誌等級 |
| `ROBOTS_POLICY` | `public` | staging 用 `private` | 是否允許索引 |
| `ENABLE_LLMS_TXT` | `false` | `false` | 實驗性相容層 |
| `WEB_CONCURRENCY` | `2` | `2` | Gunicorn worker 數 |
| `DB_POOL_SIZE` | `5` | `5` | 連線池 |
| `DB_MAX_OVERFLOW` | `2` | `2` | 連線池溢位 |
| `AUDIT_IP_SALT` | 無 | Secret Manager | 見下 |
| `GCS_PUBLIC_BASE_URL` | 無 | CDN／LB 網址 | bucket 私有時必填 |

### 由平台自動提供

| 變數 | 來源 |
| --- | --- |
| `PORT` | Cloud Run 注入；`gunicorn.conf.py` 綁 `0.0.0.0:$PORT` |
| `GOOGLE_APPLICATION_CREDENTIALS` | Cloud Run 上不需要（用 ADC） |

---

## 幾個設定的陷阱

### `PUBLIC_BASE_URL`

canonical 與 sitemap 取自這個值，**不是**從 request 的 Host header 推導。
理由：Host header 可偽造，且反向代理下不可靠。

代價是：忘了改成正式網域時，網站**完全正常運作**，
只是所有 canonical 與 sitemap 指向錯誤位置。這是最安靜也最傷 SEO 的錯誤。
`scripts/smoke_cloud.py` 會專門檢查這一項。

### `AUDIT_IP_SALT`

AuditLog 的 IP 雜湊 salt。**未設定時系統不記錄 IP**（記 `null`）。

不要圖方便給一個固定字串：IPv4 位址空間只有 2³²，
已知 salt 的情況下可以在數分鐘內把所有雜湊反查回原始 IP，
等於沒有雜湊。要嘛用 Secret Manager 提供高熵值，要嘛不記錄。

### `STORAGE_BACKEND`

若打錯字（例如 `gcs ` 帶空白或 `GCS`），`init_storage` 會直接
raise 而不是退回 local。這是刻意的：靜默退回 local 會讓檔案寫進
Cloud Run 的暫存檔案系統並在 instance 重啟後消失 [S17] ——
那是無聲的資料遺失，遠比啟動失敗嚴重。

### `RATELIMIT_STORAGE_URI`

預設 `memory://`，代表**每個 Cloud Run instance 各自計數**。
多 instance 時實際的登入嘗試上限會是「設定值 × instance 數」。

SAI §11.2 只要求「超限暫時拒絕」，因此這是可接受的已知限制。
若需要嚴格全域限流，改用 Memorystore Redis 並設定此變數。

### `session_protection = "strong"`

Flask-Login 在 user agent 或 IP 變動時會使 session 失效。
多 instance + Load Balancer 環境下，若使用者 IP 變動（例如行動網路切換）
會被登出。這是安全性與便利性的取捨，目前選擇安全性。

---

## Gunicorn 設定

`gunicorn.conf.py`，關鍵項目：

| 設定 | 值 | 理由 |
| --- | --- | --- |
| `bind` | `0.0.0.0:$PORT` | Cloud Run 契約要求 |
| `workers` | `WEB_CONCURRENCY` (2) | 單一 worker 處理慢請求時會阻塞健康檢查 |
| `worker_class` | `gthread` | I/O 為主的負載 |
| `threads` | 4 | |
| `timeout` | 30s | 涵蓋圖片重新編碼 + 上傳 GCS |
| `keepalive` | 65s | 略長於一般 LB 閒置時間，避免 502 |
| `max_requests` | 1000 (+jitter) | 定期回收 worker，釋放 Pillow 的記憶體累積 |
| `preload_app` | `False` | 避免 fork 前建立 DB 連線 |
| `accesslog`/`errorlog` | `-` | stdout/stderr，由 Cloud Logging 收集 |
| `forwarded_allow_ips` | `*` | Cloud Run 在反向代理後，需信任 X-Forwarded-* |

存取日誌格式刻意**不記錄** request body 與 cookie（SAI §19）。

---

## 資源建議

研究室網站流量低：

| 項目 | 建議 |
| --- | --- |
| CPU | 1 |
| Memory | 512Mi |
| minScale | 0（可接受冷啟動）或 1（避免冷啟動） |
| maxScale | 4 |
| containerConcurrency | 40 |
| Cloud SQL tier | `db-f1-micro` |

> Cloud SQL 是持續計費資源。建立後請立刻設定 budget alert（SAI §24）。

---

## 自訂網域

Google 對正式 custom domain 的首選是 **global external Application
Load Balancer**；Cloud Run 原生 domain mapping 仍屬 Preview／limited
availability，**不列為 production baseline** [S22]。

校方 DNS 完成前可用 `run.app` 做 staging，並設 `ROBOTS_POLICY=private`。

---

## 部署後必做

```bash
python scripts/smoke_cloud.py https://<網域>
```

23 項檢查，涵蓋健康、公開頁、SEO、安全 headers、Admin 保護、
cookie 旗標與 404 行為。任一失敗即不得切換 traffic（SAI §21.6）。
