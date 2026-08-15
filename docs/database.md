# 資料模型

> 對應 SAI §8（資料模型）與 §10.2（Database Portability Contract）。
> 設計原則：**小而穩**。最重要的是人物與成果的長期關聯，
> 而不是建立通用 CMS。

---

## ER 關係

```
AdminUser (v1 僅 1 筆)
└── writes ──> AuditLog

Person 1 ─────< ResearchOutputPerson >───── 1 ResearchOutput
  │                                              │
  │ status = faculty / current / alumni          │ output_type = journal /
  │ 穩定的 /people/<slug>                         │   conference / project /
  │                                              │   prototype / simulation /
  │                                              │   dataset / other
  └── 畢業只改 status，不搬資料                     └── 穩定的 /research/<slug>

SiteSetting (singleton, id=1)
Redirect (old_path -> new_path)
```

共 **7 張核心表**。

---

## 各表用途

| 表 | 用途 | 關鍵約束 |
| --- | --- | --- |
| `admin_users` | 單一管理帳號 | `username` unique；只存 password hash |
| `people` | 教授／在學生／畢業生（同一實體） | `slug` unique |
| `research_outputs` | 所有研究成果，以 `output_type` 分流 | `slug` unique |
| `research_output_people` | 人物與成果的關聯 | `(research_output_id, person_id)` unique |
| `site_settings` | 站台設定（singleton row id=1） | — |
| `redirects` | slug 變更後的舊網址轉址 | `old_path` unique |
| `audit_logs` | 管理操作稽核 | — |

---

## 索引（SAI §8.9）

實際建立於 migration，可用 `sqlite_master` 或 `\di` 查證：

| 表 | 索引 | 支援的查詢 |
| --- | --- | --- |
| `people` | `ix_people_slug` | 個人頁 slug 查詢 |
| `people` | `ix_people_status_publish_sort` | /members、/alumni 列表 |
| `people` | `ix_people_graduation_year` | 畢業生依年度分組 |
| `people` | `ix_people_legacy_id` | legacy 遷移對應 |
| `research_outputs` | `ix_research_outputs_slug` | 成果頁 |
| `research_outputs` | `ix_research_publish_year_type` | /research 篩選 |
| `research_outputs` | `ix_research_featured` | 首頁精選 |
| `research_outputs` | `ix_research_outputs_doi` | DOI 去重 |
| `research_output_people` | 兩個 FK 各一 | 雙向導航 |
| `audit_logs` | action / entity_type / created_at / admin_user_id | 稽核查詢 |

---

## 可攜性契約（SAI §10.2）

這些規則讓同一份程式碼能在 SQLite 與 PostgreSQL 上行為一致。
`tests/test_db_portability.py` 以自動化方式強制執行。

### 1. 不在 route / service 寫 raw SQL

所有 CRUD 走 ORM / repository。唯一例外是健康檢查的 `SELECT 1`，
且測試會斷言它只能是 `SELECT 1`。

### 2. SQLite-only 技巧必須隔離

`PRAGMA` 只允許出現在 `app/extensions.py` 的連線事件中：

```python
PRAGMA foreign_keys=ON     # SQLite 預設不強制外鍵，開啟以對齊 PostgreSQL
PRAGMA busy_timeout=15000  # 降低本機並行寫入的 "database is locked"
```

禁止使用 `strftime()`、`julianday()`、`group_concat()`、
`datetime('now')`、`sqlite_version()` —— PostgreSQL 沒有相同語意。

### 3. 約束必須在 migration 明確定義

Primary key、foreign key、unique、nullable、length、check
全部寫在 migration，不依賴 SQLite 的寬鬆型別。

**不使用 native ENUM**。狀態欄位一律 `String` + `CheckConstraint`：

```python
sa.CheckConstraint(
    "status IN ('faculty', 'current', 'alumni')",
    name="ck_people_status",
)
```

原因：PostgreSQL 的 native ENUM 型別變更需要 `ALTER TYPE`，
在 migration 中處理麻煩且不可逆；String + CHECK 兩邊行為一致。

### 4. 時間一律 UTC

所有 datetime 欄位使用 `UtcDateTime`（`app/models/mixins.py`），
它是 `TypeDecorator`，在讀寫兩端都強制轉換：

- 寫入：naive 視為 UTC 補 tzinfo → 轉 UTC 存入
- 讀取：naive 補 UTC tzinfo → 一律回傳 aware

前台顯示時才轉 Asia/Taipei（`app/utils/dates.py`）。

**禁止使用 `datetime.utcnow()`**（回傳 naive）。
一律用 `app.models.mixins.utcnow()`。這條規則由測試強制。

> 實作陷阱：`UtcDateTime.python_type` 會 raise `NotImplementedError`
> （SQLAlchemy 的 `TypeDecorator` 不委派此屬性）。任何需要判斷
> 欄位型別的工具都必須主動展開 `.impl`，
> 見 `scripts/import_postgres.py::_column_python_type`。

### 5. Alembic 必須雙 DB 可用

`render_as_batch` 依 dialect 自動切換（SQLite 需要 batch mode
重建資料表才能改欄位；PostgreSQL 原生支援 ALTER）。

---

## Migration

```bash
flask db migrate -m "描述"   # 產生
flask db upgrade             # 套用
flask db current             # 查看目前 revision
flask db downgrade -1        # 回退一版
```

**autogenerate 產生的 migration 必須人工檢查**，特別注意
是否誤用 `sa.Enum()`、nullable/length 是否明確。

對 PostgreSQL 驗證：

```bash
TEST_POSTGRES_URL=postgresql+psycopg://... pytest -m postgres
```

---

## SQLite → PostgreSQL 遷移

工具鏈與完整程序見 `deploy/migration-runbook.md`。

```bash
python scripts/export_sqlite.py --output ./migration-package
python scripts/import_postgres.py --package ./migration-package --dry-run
python scripts/import_postgres.py --package ./migration-package
```

### 兩個容易踩的坑

**1. datetime 表示法**
SQLite 沒有原生 DATETIME，原生 `sqlite3` driver 回傳文字。
匯出必須透過 SQLAlchemy 讀取，才會經過 `UtcDateTime` 的
`process_result_value` 得到 aware datetime，也才能讓兩端
checksum 可比對。

**2. PostgreSQL sequence**
帶著明確 `id` 插入時 sequence 不會自動前進。若不重設，
上線後新增的第一筆內容會拿到 `id=1` 並違反主鍵約束 ——
而且匯入當下完全看不出來。
`import_postgres.py` 會自動 `setval` 到 `max(id)`。

---

## 隱私與稽核

- **不在 DB 儲存**：raw password、session secret、API credential
- `audit_logs.summary` 不得包含密碼或敏感值
- IP 記錄：需設定 `AUDIT_IP_SALT` 才會記錄雜湊。
  未設定時記 `null` —— 使用固定或可猜測的 salt 等同沒有雜湊，
  因為 IPv4 空間只有 2³²，可完整反查
- 畢業生的 `current_affiliation` / `current_position`
  只在本人確認可公開時填寫

---

## 備份

```bash
python scripts/backup_sqlite.py                  # 建立
python scripts/backup_sqlite.py --verify-latest  # 還原演練（AC-14）
```

備份使用 SQLite Online Backup API 建立一致快照（不是直接複製檔案），
DB 與 uploads 打包在同一個 unit，並記錄 checksum 與 schema revision。

正式環境的備份責任轉由 Cloud SQL 自動備份與 Cloud Storage 版本控管承擔。
