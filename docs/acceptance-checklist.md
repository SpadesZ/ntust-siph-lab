# 驗收清單 AC-01 ~ AC-26

> 對應 SAI §20.1 與附錄 C。cutover 前必須全部通過。
>
> 每一項標註**如何驗證**。能自動化的都已自動化：
> `pytest -m acceptance` 會執行對應的測試。
> 無法自動化的（真實瀏覽器、Docker 持久性）標註為人工項目。

---

## 自動化執行

```bash
pytest -m acceptance          # AC 對應測試
pytest                        # 全部測試
python scripts/verify_migration.py   # AC-21 ~ AC-26
python scripts/backup_sqlite.py --verify-latest   # AC-14
python scripts/smoke_cloud.py <url>  # 部署後
```

---

## 認證與授權

| AC | 敘述 | 驗證方式 | 狀態 |
| --- | --- | --- | --- |
| AC-01 | 未登入進 `/admin` → `/admin/login` | `pytest -m acceptance -k ac01`；smoke test | ☐ |
| AC-02 | 錯密碼不建立 session；連續錯誤觸發 rate limit | `pytest -k ac02` | ☐ |
| AC-03 | 正確帳密登入後 `/admin` 可見 | `pytest -k ac03` | ☐ |

> AC-01 的完整保證來自 admin blueprint 的全域 `before_request`
> + `login_required`，因此新增 route 自動受保護。
> `tests/test_admin.py::test_every_admin_route_requires_login`
> 會列舉 url_map 中所有 admin route 逐一驗證，不依賴人工維護清單。

## 內容 CRUD 與狀態

| AC | 敘述 | 驗證方式 | 狀態 |
| --- | --- | --- | --- |
| AC-04 | 新增 Current Member，發布後出現在 `/members` | `pytest -k ac04` | ☐ |
| AC-06 | current → alumni，id/slug 不變，`/members` 消失、`/alumni` 出現 | `pytest -k ac06`（含 HTTP route 版本） | ☐ |
| AC-07 | ResearchOutput 關聯 Person 後雙向可導航 | `pytest -k ac07` | ☐ |
| AC-08 | draft 不出現在 public / sitemap | `pytest -k ac08` | ☐ |
| AC-09 | published 出現在 `/research` 與 sitemap | `pytest -k ac09` | ☐ |
| AC-10 | 修改 published slug 產生 301 redirect | `pytest -k ac10` | ☐ |

## 媒體與驗證

| AC | 敘述 | 驗證方式 | 狀態 |
| --- | --- | --- | --- |
| AC-11 | 上傳非法副檔名被拒絕 | `pytest -k ac11` | ☐ |
| AC-12 | 有圖但無 alt 時 publish validator 阻擋 | `pytest -k ac12` | ☐ |

> AC-11 的保證不只靠副檔名：`MediaService` 會以 Pillow 實際解碼，
> 偽裝成 `image/jpeg` 的 `.php` 或含 `<script>` 的 `.svg` 都會被拒。

## 基礎設施

| AC | 敘述 | 驗證方式 | 狀態 |
| --- | --- | --- | --- |
| AC-13 | docker image rebuild 後 DB 與 uploads 不消失 | **人工**：見下方程序 | ☐ |
| AC-14 | backup + restore drill 後 `integrity_check` pass | `python scripts/backup_sqlite.py --verify-latest` | ☐ |
| AC-20 | production 啟動使用 Gunicorn，不是 `flask run` | `pytest -k ac20`（靜態）＋ **人工**確認容器行程 | ☐ |

### AC-13 人工驗證程序

```bash
docker compose up -d
docker compose exec web flask db upgrade
docker compose exec web flask seed legacy
# 上傳一張照片並記下網址

docker compose down
docker compose build --no-cache
docker compose up -d

# 確認：人物資料仍在、照片仍可載入
curl -s http://127.0.0.1:8000/members | grep 陳泓序
```

**通過條件**：重建後資料與圖片都在（靠三個 bind mount）。

### AC-20 人工驗證

```bash
docker compose exec web ps aux | grep gunicorn
# 應看到 gunicorn master + worker，不得看到 flask run / werkzeug
```

## 前端品質

| AC | 敘述 | 驗證方式 | 狀態 |
| --- | --- | --- | --- |
| AC-15 | 手機 320px 無 body horizontal scroll | **人工**：DevTools 設 320px 逐頁檢查 | ☐ |
| AC-16 | 鍵盤可完成 admin login 與主要表單 | **人工**：只用 Tab/Enter 完成登入與新增成員 | ☐ |
| AC-18 | 首頁 H1 唯一，主導覽與 footer 可鍵盤使用 | `pytest -k ac18`（H1）＋ **人工**（鍵盤） | ☐ |

### AC-15 / AC-16 人工程序

**320px 檢查**：Chrome DevTools → 裝置模擬 → 寬度 320px，
逐一開啟 `/`、`/about`、`/members`、`/research`、`/alumni`、`/join`、
人物頁、成果頁、`/admin/login`、後台表單。
確認：無水平捲軸、文字不溢出、表格可捲動、觸控目標夠大。

**鍵盤檢查**：不用滑鼠，從 `/admin/login` 開始：
Tab 到帳號 → 密碼 → Enter 登入 → Tab 到「新增成員」→
填完表單 → 送出。全程 focus ring 必須清楚可見。

## SEO / 結構化資料

| AC | 敘述 | 驗證方式 | 狀態 |
| --- | --- | --- | --- |
| AC-05 | Person detail 有 canonical、title、description、Person JSON-LD | `pytest -k ac05` | ☐ |
| AC-17 | structured data 不含頁面看不到的虛構欄位 | `pytest -k ac17`；Rich Results Test 人工複核 | ☐ |

> AC-17 的自動化只能驗證「JSON-LD 可解析、型別正確、
> 麵包屑與可見內容同源」。「內容是否屬實」無法自動驗證，
> 必須由教授在 sign-off 時人工確認。

## 錯誤處理

| AC | 敘述 | 驗證方式 | 狀態 |
| --- | --- | --- | --- |
| AC-19 | 500 頁不顯示 stack trace | `pytest -k ac19` | ☐ |

## Legacy 零遺漏遷移

| AC | 敘述 | 驗證方式 | 狀態 |
| --- | --- | --- | --- |
| AC-21 | inventory 所有 LC 項目有 source/type/target/status，無空白 | `verify_migration.py` | ☐ |
| AC-22 | Legacy Baseline 100% MIGRATED/APPROVED_*，UNRESOLVED = 0 | `verify_migration.py` | ☐ |
| AC-23 | 教授姓名/職稱/學歷/六專長/Email/外鏈/四位學生可在新站找到 | `verify_migration.py` + `pytest tests/test_legacy_migration.py` | ☐ |
| AC-24 | 教授圖片有 manifest、checksum 或人工驗證紀錄 | `verify_migration.py` | ☐ |
| AC-25 | 平台 chrome 已排除；Calendar embed 有明確決策 | `verify_migration.py` | ☐ |
| AC-26 | difference_report 無未解決缺漏；舊站在 sign-off 前不關閉 | `verify_migration.py` + **人工**確認舊站狀態 | ☐ |

> AC-26 的後半段（舊站狀態）是程序性條件，程式無法驗證。
> 見 `deploy/migration-runbook.md` 階段 10 的順序要求。

---

## Cutover 前最終確認

### 技術

- [ ] `pytest` 全綠（0 failed）
- [ ] `pytest -m acceptance` 全綠
- [ ] PostgreSQL 測試**實際執行過**（非 skip）
- [ ] `verify_migration.py`：UNRESOLVED = 0
- [ ] backup restore drill 通過
- [ ] AC-13 人工驗證通過
- [ ] AC-15 / AC-16 人工驗證通過
- [ ] `smoke_cloud.py` 對正式網址全過

### 內容（由教授／管理者確認）

- [ ] 教授姓名、職稱、學歷正確
- [ ] 六項研究專長完整且無錯字
- [ ] Email 正確
- [ ] NTUST 外鏈可到達
- [ ] 教授照片正確且有替代文字
- [ ] 四位碩二生姓名正確
- [ ] 所有已發布的研究成果資訊屬實（DOI／連結點得開）
- [ ] 畢業生就業資訊皆已取得本人同意
- [ ] `content_signoff.md` 已簽署

### 順序

- [ ] 上述全部完成**之後**，才把舊 Google Sites 改成搬遷公告

---

## 目前狀態

**最近一次驗收：2026-08-15（獨立審查 + 修復後複驗）**

### 自動化測試

```
pytest              -> 664 passed, 3 skipped
pytest -m acceptance -> 93 passed
```

3 項 skip 全部是 PostgreSQL 測試（未設定 `TEST_POSTGRES_URL`）。

### 逐項狀態

| AC | 狀態 | 依據 |
| --- | --- | --- |
| AC-01 | ✅ 已驗證 | 22 條 admin route 逐一探測，零外洩 |
| AC-02 | ✅ 已驗證 | 實測狀態序列 `[200,200,200,429]` |
| AC-03 | ✅ 已驗證 | 實機登入 → `/admin` 200 |
| AC-04 | ✅ 已驗證 | 建立→草稿不可見→發布→出現於 /members |
| AC-05 | ✅ 已驗證 | canonical / title / description / Person JSON-LD 齊備 |
| AC-06 | ✅ 已驗證 | 經真實 HTTP route；slug 不變、成果關聯保留 |
| AC-07 | ✅ 已驗證 | 雙向連結實測 |
| AC-08 | ✅ 已驗證 | 草稿不在列表、sitemap，detail 404 |
| AC-09 | ✅ 已驗證 | 發布後出現於列表與 sitemap |
| AC-10 | ✅ 已驗證 | 舊 slug 301 導向新 slug |
| AC-11 | ✅ 已驗證 | `.php`、`.svg`、polyglot 檔案皆被拒 |
| AC-12 | ✅ 已驗證 | 有圖無 alt 阻擋發布 |
| **AC-13** | ⬜ **未驗證** | **Docker engine 在審查環境無法啟動。設定（三個 bind mount）靜態檢查正確，但從未實證。上線前必須執行本文件的人工程序。** |
| AC-14 | ✅ 已驗證 | integrity_check ok、FK 無孤兒、checksum 相符；另測損毀備份會被偵測 |
| AC-15 | ⬜ 未驗證 | viewport meta 存在；無真實瀏覽器 320px 檢查 |
| AC-16 | ⬜ 未驗證 | 語意標記與 focus 樣式齊備；無真實鍵盤操作 |
| AC-17 | ⚠️ 部分 | JSON-LD 有效、型別正確、麵包屑同源；「內容是否屬實」需人工 sign-off |
| AC-18 | ⚠️ 部分 | H1 唯一已驗證；鍵盤同 AC-16 |
| AC-19 | ⚠️ 部分 | 404 無 stack trace 已驗證；未觸發真正的 500 |
| **AC-20** | ⬜ **未驗證** | Dockerfile `CMD gunicorn` 靜態正確且無 `flask run`；gunicorn 無法在 Windows 執行，未實跑 |
| AC-21 | ✅ 已驗證 | 19 筆欄位完整 |
| AC-22 | ✅ 已驗證 | UNRESOLVED = 0 |
| AC-23 | ✅ 已驗證 | 17 項母站內容實測渲染於前台 |
| AC-24 | ✅ 已驗證 | LC-002 sha256 與宣稱值逐位元相符 |
| AC-25 | ✅ 已驗證 | 平台元素排除；LC-019 有核准紀錄 |
| AC-26 | ⚠️ 部分 | 報告 0 unresolved；「舊站不得提前關閉」屬程序條件 |

**統計：已驗證 18／部分 4／未驗證 4／失敗 0**

### 上線前必須補做

1. **PostgreSQL 雙 DB 矩陣（Gate G2）** — 目前 3 項測試 skip
   ```bash
   docker run -d --name siph-pg -e POSTGRES_PASSWORD=pw -p 5432:5432 postgres:16
   docker exec siph-pg psql -U postgres -c "CREATE DATABASE siph_test;"
   TEST_POSTGRES_URL=postgresql+psycopg://postgres:pw@localhost:5432/siph_test pytest -m postgres
   ```
2. **AC-13 容器重建持久性** — 依本文件的人工程序執行
3. **AC-20 gunicorn 實跑** — `docker compose exec web ps aux | grep gunicorn`
4. **AC-15 / AC-16 人工檢查** — 320px 與純鍵盤操作
5. **內容 sign-off** — 教授核對後簽署 `content_signoff.md`
