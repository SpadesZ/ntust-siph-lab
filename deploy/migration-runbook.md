# Cutover Runbook：Google Sites → Cloud Run

> 對應 SAI §21（Production Migration）與 §22（零遺漏遷移規範）。
> 本文是 cutover 當天照著逐行執行的操作手冊，含 rollback 程序。
>
> **最重要的一條規則（ADR-011）**：舊 Google Sites 在 content sign-off
> 完成之前**不得關閉，也不得改成搬遷公告**。任何未解決的遷移項目
> 都會阻擋 cutover。

---

## 角色與前置

| 角色 | 職責 |
| --- | --- |
| 操作者 | 執行本手冊指令 |
| 教授／研究室管理者 | 內容核對與 sign-off（不可由 Agent 或工程人員代簽） |

預估時間：2–3 小時（不含 DNS 生效等待）。
建議在低流量時段執行。

---

## 階段 0：Gate 檢查（不可跳過）

```bash
# G1 Feature freeze
pytest
pytest -m acceptance

# G2 DB portability
TEST_POSTGRES_URL=postgresql+psycopg://user:pw@localhost/siph_test pytest -m postgres

# G6 Legacy No-Loss
python scripts/verify_migration.py
```

**通過條件**

- [ ] `pytest` 全綠，無 failed
- [ ] PostgreSQL 測試實際執行（不是 skip）
- [ ] `difference_report.md` 的 UNRESOLVED = **0**
- [ ] `content_signoff.md` 已由教授／管理者簽署
- [ ] AC-21 ~ AC-26 全部通過

> 任一項未過 → **停止**。這些 gate 的存在就是為了在此刻擋下。

---

## 階段 1：建立 rollback point（G5）

```bash
python scripts/backup_sqlite.py
python scripts/backup_sqlite.py --verify-latest
```

**通過條件**

- [ ] `integrity_check: ok`
- [ ] `foreign_key_check` 無孤兒
- [ ] 媒體 checksum 全部相符
- [ ] 備份檔已複製到**另一台機器或雲端硬碟**（off-host copy）

> 這份備份是整個 cutover 唯一的資料保險。SAI §21.6 明確指出
> 「資料 migration 需有獨立 rollback/forward-fix 計畫，
> 不能只依賴 container rollback 還原資料庫」。

---

## 階段 2：產生 migration package（G3 / G4）

```bash
python scripts/export_sqlite.py --output ./migration-package
```

**記錄下列數值**（rollback 與事後稽核需要）：

| 項目 | 值 |
| --- | --- |
| schema revision | `________` |
| people 筆數 / checksum | `________` |
| research_outputs 筆數 / checksum | `________` |
| 媒體物件數 | `________` |

---

## 階段 3：建立雲端 schema

見 `deploy/cloudrun.md` 第 1、3 節。

```bash
cloud-sql-proxy "${PROJECT_ID}:${REGION}:siph-lab-db" &
export DATABASE_URL="postgresql+psycopg://siph_app:<pw>@127.0.0.1:5432/siph_lab"
flask db upgrade
flask db current      # 必須等於階段 2 記錄的 revision
```

**通過條件**

- [ ] `flask db current` 與 package 的 schema revision **完全相同**

---

## 階段 4：匯入資料

```bash
# 先 dry-run：完整執行所有驗證但最後 rollback
python scripts/import_postgres.py --package ./migration-package \
  --database-url "$DATABASE_URL" --dry-run
```

**dry-run 必須全綠才繼續。** 若出現 checksum 不符，停止並回報 —— 
那代表匯出/匯入的型別處理有問題，硬幹會產生「看起來成功但內容悄悄改變」
的最壞情況。

```bash
# 正式匯入
python scripts/import_postgres.py --package ./migration-package \
  --database-url "$DATABASE_URL"
```

**通過條件**

- [ ] 所有表筆數與 package 相符
- [ ] 所有表內容 checksum 相符
- [ ] slug 唯一、無空值
- [ ] 無孤兒關聯
- [ ] sequence 已重設（輸出會列出）

---

## 階段 5：同步媒體

```bash
export GCS_BUCKET="${PROJECT_ID}-siph-media"
python scripts/sync_media_to_gcs.py --dry-run
python scripts/sync_media_to_gcs.py --report ./media-sync-report.csv
```

**通過條件**

- [ ] failed = 0
- [ ] 上傳數 + 略過數 = package 中的媒體物件總數
- [ ] `media-sync-report.csv` 已存檔（G4 證據）

---

## 階段 6：建立管理員並複驗

```bash
flask admin create --username <admin> --generate     # 密碼只顯示一次
python scripts/verify_migration.py                    # 對雲端資料再跑一次
```

---

## 階段 7：部署與 smoke test

```bash
gcloud run deploy ... --no-traffic      # 見 deploy/cloudrun.md 第 6 節
python scripts/smoke_cloud.py "$REV_URL"
```

**通過條件**

- [ ] smoke test 全部通過（23 項）
- [ ] canonical 與 sitemap 指向正式網域
- [ ] session cookie 有 Secure 旗標

---

## 階段 8：持久性驗證（AC-13 的雲端對應）

Cloud Run 的 container filesystem 不持久 [S17]。必須實證資料不在其中：

```bash
# 強制建立新 instance
gcloud run services update "$SERVICE" --region="$REGION" \
  --update-env-vars="_FORCE_RESTART=$(date +%s)"

# 等新 revision 就緒後重新檢查
python scripts/smoke_cloud.py "$REV_URL"
```

**通過條件**

- [ ] 重啟後人物、成果、圖片全部仍在
- [ ] 後台可正常登入

---

## 階段 9：切換 traffic

```bash
gcloud run services update-traffic "$SERVICE" --region="$REGION" --to-latest
python scripts/smoke_cloud.py "https://<正式網域>"
```

---

## 階段 10：Search Console 與舊站處理

**順序不可顛倒**：

1. 正式網域可正常存取並通過 smoke test
2. Search Console 驗證網域擁有權、提交 `sitemap.xml`
3. 教授／管理者最終目視確認（人物、專長、Email、外鏈、照片）
4. **確認 `content_signoff.md` 已簽署**
5. 才可以把舊 Google Sites 改成搬遷公告並指向新網址

> AC-26：「舊站不得在 sign-off 前關閉或改成搬遷公告。」
> 這是防止「新站有缺漏但舊站已經沒了」的最後一道防線。

---

## Rollback 程序

### 情境 A：部署後發現問題，資料未損壞

```bash
# 切回上一個 known-good revision
gcloud run services update-traffic "$SERVICE" --region="$REGION" \
  --to-revisions=<previous-revision>=100
```

適用於：程式錯誤、設定錯誤、效能問題。
**不適用於**資料問題 —— container rollback 不會還原資料庫。

### 情境 B：資料匯入錯誤

```bash
# 1. 切回舊 revision（或維持舊 Google Sites 為對外入口）
# 2. 清空並重新匯入
python scripts/import_postgres.py --package ./migration-package \
  --database-url "$DATABASE_URL" --truncate
```

`--truncate` 會先清空所有表再匯入。因為本機 SQLite 仍是完整的
真實來源（階段 1 的備份），重跑是安全的。

### 情境 C：完全放棄 cutover

1. 舊 Google Sites 尚未關閉（依 AC-26 必然如此），維持原狀即可
2. Cloud Run service 設為 `--no-traffic` 或刪除
3. 本機 SQLite + uploads 未受影響，繼續在本機修正
4. Cloud SQL instance 可保留（下次重試）或刪除（省成本）

> 因為 SAI 強制「舊站在 sign-off 前不得關閉」，情境 C 的
> 對外影響是**零**。這正是那條規則的價值。

---

## Cutover 紀錄表

| 項目 | 值 / 時間 | 執行者 |
| --- | --- | --- |
| Gate 全部通過 | | |
| 備份檔案名稱 | | |
| 備份 off-host 位置 | | |
| schema revision | | |
| 資料匯入完成 | | |
| 媒體同步完成（物件數） | | |
| smoke test 通過 | | |
| 持久性驗證通過 | | |
| traffic 切換 | | |
| Search Console 提交 | | |
| content sign-off 簽署人／日期 | | |
| 舊站改為搬遷公告 | | |
