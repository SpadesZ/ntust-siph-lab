# 本機開發指南

> 對應 SAI §10（Local-first 資料持久化）與 §23（開發階段）。
>
> **本階段完全不需要 GCP 資源。** ADR-002 明定開發、設計 review、
> 內容輸入與功能驗收全部先在本機完成，不要求先支付 Cloud SQL 成本。

---

## 環境需求

- Python 3.12
- Docker Desktop（使用 compose 時）
- Git

---

## 兩種啟動方式

### A. Docker Compose（與正式環境同一份 image）

```bash
docker compose up --build -d
docker compose exec web flask db upgrade
docker compose exec web flask admin create --username admin --generate
docker compose exec web flask seed legacy
```

資料持久化靠三個 bind mount（`./instance`、`./uploads`、`./backups`），
因此 `docker compose down && build && up` 之後資料不會消失（AC-13）。

### B. 原生 Python

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env.local

export FLASK_APP=wsgi.py
flask db upgrade
flask admin create --username admin --generate
flask run --port 8000
```

> `flask run` 只能用於開發。正式環境一律 Gunicorn（AC-20）。

---

## 環境變數

完整清單見 `.env.example`。開發階段只有這幾個常需調整：

| 變數 | 預設 | 說明 |
| --- | --- | --- |
| `APP_ENV` | `local` | 決定使用哪個 Config class |
| `SECRET_KEY` | dev 固定值 | local 可用弱值；production 必須高熵 |
| `DATABASE_URL` | `instance/siph_lab.db` | SQLite 路徑 |
| `UPLOAD_DIR` | `uploads/` | 媒體根目錄 |
| `PUBLIC_BASE_URL` | `http://localhost:8000` | canonical 與 sitemap 的根 |
| `ROBOTS_POLICY` | `public` | staging 請設 `private` |
| `ENABLE_LLMS_TXT` | `false` | 實驗性相容層，預設關閉 |
| `AUDIT_IP_SALT` | 無 | AuditLog 的 IP 雜湊 salt（見下） |

> **`AUDIT_IP_SALT`**：未設定時 AuditLog 不會記錄 IP 雜湊（改記 `null`），
> 這是刻意的隱私安全預設。若要啟用 IP 記錄，必須提供高熵 salt ——
> 使用可猜測的值等同沒有雜湊，因為 IPv4 空間只有 2³²，
> 已知 salt 下可在數分鐘內完全反查。

### 環境變數讀取時機（重要）

`app/config.py` 的設定值在 **模組 import 時** 求值。
`wsgi.py` 已確保 `.env` 先於 `app` import 載入。
若你寫自訂腳本，請在 `from app import ...` **之前**設定環境變數，
否則會拿到預設值而不是你設的值。

---

## 資料庫

### schema 變更

```bash
# 修改 app/models/*.py 之後
flask db migrate -m "描述變更"
# 檢查產生的 migration 是否正確（autogenerate 不完美）
flask db upgrade
```

**必須檢查產生的 migration**，特別是：

- 是否誤用 `sa.Enum()`（禁止；請用 String + CheckConstraint）
- nullable / length / unique 是否明確定義
- SQLite 需要 batch mode（已由 `extensions.py` 依 dialect 自動設定）

### 對 PostgreSQL 測試（SAI §10.2 要求）

```bash
docker run -d --name siph-pg -e POSTGRES_PASSWORD=pw -p 5432:5432 postgres:16
docker exec siph-pg psql -U postgres -c "CREATE DATABASE siph_test;"

export TEST_POSTGRES_URL="postgresql+psycopg://postgres:pw@localhost:5432/siph_test"
pytest -m postgres
```

> 未設定 `TEST_POSTGRES_URL` 時這些測試會 **skip 而非通過**。
> 上線前（Gate G2）必須實際跑過一次。

---

## 測試

```bash
pytest                    # 全部
pytest -m acceptance      # 只跑 AC-01~AC-26 對應測試
pytest -m "not slow"      # 跳過慢速
pytest --cov=app          # 覆蓋率
pytest tests/test_seo.py -v
```

---

## 內容管理

```bash
flask seed legacy          # 匯入母站 LC-001~LC-019
flask seed legacy --force  # 覆寫既有 legacy_id 對應資料
flask check publish        # 批次檢查發布門檻
```

`flask seed legacy` 是冪等的，可重複執行。
它**不會**產生研究成果或畢業生 —— 母站沒有這些資料，
依 SAI §2.3 不得推測產生。

---

## 分享預覽圖（OG image）

```bash
python scripts/generate_og_image.py   # 產生 app/static/img/og-default.png
```

當管理者沒有在後台上傳自訂 OG 圖片時，全站分享預覽（LINE、
Facebook、Slack 等）會回退到這張內建圖。人物頁與研究成果頁
另有自己的圖片，不受影響。

圖上的文字讀自 SiteSetting 的**實驗室名稱 / 所屬學校 / 系所**，
但 PNG 是 build-time 產物：**在後台改完這三個欄位之後，
必須重跑本腳本並 commit 新的 PNG**，否則分享卡片會停留在舊值。

需要系統上有 CJK 字體（Windows 的微軟正黑體、macOS 的 PingFang
或 Linux 的 Noto Sans CJK）。找不到字體或資料庫讀不到時會直接
報錯，不會產生一張全是豆腐方塊的圖。

---

## 備份與還原演練（AC-14）

```bash
python scripts/backup_sqlite.py                  # 建立備份
python scripts/backup_sqlite.py --verify-latest  # 還原演練
```

還原演練會在全新暫存目錄還原並檢查：
manifest 版本、DB checksum、`PRAGMA integrity_check`、
`PRAGMA foreign_key_check`、各表筆數、schema revision、媒體 checksum。

建議在每次重要內容更新後執行一次。

---

## 常見問題

**Q: `docker compose up` 說找不到 `.env.local`？**
不會了。`.env.local` 現在是選用的（`required: false`），
compose 內含本機預設值。需要覆寫時才 `cp .env.example .env.local`。

**Q: `flask admin create` 顯示「已存在啟用中的管理員帳號」？**
SAI §8.2 限制 v1 只能有一個 active admin。
忘記密碼請用 `flask admin reset-password --username X --generate`。

**Q: 上傳圖片被拒絕？**
只接受 jpg/jpeg/png/webp，且必須是「真的能被 Pillow 解碼」的影像。
副檔名偽裝會被擋（SAI §16）。上限 8 MB。

**Q: 內容建立了但前台看不到？**
新建內容預設是 draft。要在編輯頁按「發布」，
且必須通過 publish validator（例如有圖就必須有 alt）。

**Q: 前台顯示的時間怪怪的？**
DB 一律存 UTC，前台以 Asia/Taipei 顯示（SAI §8.9）。
若看到 UTC 時間，檢查是否繞過了 `local_datetime` filter。

**Q: 改了 CSS 但瀏覽器沒更新？**
靜態資產有長 cache。開發時用 Ctrl+F5 強制重新載入。

---

## 相關文件

- `docs/content-guide.md` — 給非工程人員的內容維護指南
- `docs/database.md` — 資料模型說明
- `docs/acceptance-checklist.md` — 驗收清單
- `deploy/cloudrun.md` — 正式部署（開發階段不需要）
