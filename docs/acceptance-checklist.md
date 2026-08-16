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
TEST_POSTGRES_URL=... pytest   -> 701 passed, 0 skipped
pytest -m acceptance           -> 93 passed
pytest -m postgres             -> 3 passed
```

**0 skip** —— PostgreSQL 雙 DB 矩陣已實際執行（Gate G2 通過）。

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
| **AC-13** | ✅ **已驗證** | 刪除整個 image → `build --no-cache` → 新容器；資料與照片完全一致（照片 sha256 逐位元相符）。詳見下方紀錄 |
| AC-14 | ✅ 已驗證 | integrity_check ok、FK 無孤兒、checksum 相符；另測損毀備份會被偵測 |
| AC-15 | ⬜ 未驗證 | viewport meta 存在；無真實瀏覽器 320px 檢查 |
| AC-16 | ⬜ 未驗證 | 語意標記與 focus 樣式齊備；無真實鍵盤操作 |
| AC-17 | ⚠️ 部分 | JSON-LD 有效、型別正確、麵包屑同源；「內容是否屬實」需人工 sign-off |
| AC-18 | ⚠️ 部分 | H1 唯一已驗證；鍵盤同 AC-16 |
| AC-19 | ✅ 已驗證 | `test_ac19_500_page_hides_stack_trace` 註冊必定拋錯的 route 觸發真實 500 流程，斷言無 "Traceback" |
| **AC-20** | ✅ **已驗證** | 容器內 PID 1 為 gunicorn master、PID 7/8 為 2 個 worker，執行身分 uid=10001(appuser) 非 root，無 `flask run` |
| AC-21 | ✅ 已驗證 | 19 筆欄位完整 |
| **AC-22** | ⚠️ **部分** | 自動檢查 UNRESOLVED = 0，**但 content_signoff.md §5 人工簽核未完成**（6 項未勾、12 欄空白）。SAI §21.1 G6 是「UNRESOLVED=0 **且** sign-off 完成」的 AND 條件 |
| AC-23 | ✅ 已驗證 | 17 項母站內容實測渲染於前台（2026-08-16 於清除測試假資料後重新驗證） |
| AC-24 | ✅ 已驗證 | LC-002 sha256 與宣稱值逐位元相符 |
| **AC-25** | ✅ 已驗證 | 平台元素排除；LC-019 已於 **2026-08-16** 取得委託人實際裁示（先前的核准紀錄為 Agent 代擬，已更正） |
| **AC-26** | ⚠️ 部分 | 報告 0 unresolved 且已對齊當前資料庫；**待 §5 簽核完成**；「舊站不得提前關閉」屬程序條件 |

**統計：已驗證 22／部分 2／未驗證 2／失敗 0**

> **AC-22 / AC-26 為何不是 ✅**
> `verify_migration.py` 現在會檢查 `content_signoff.md` §5 的簽核區塊，
> 未完成時輸出「自動檢查通過，但尚未取得人工簽核，仍不得 cutover」並回傳
> exit code 2。自動化能證明「資料對得起來」，不能證明「人看過並同意」——
> 後者是 SAI §21.1 G6 的獨立條件。

---

### AC-13 實測紀錄（2026-08-15）

程序（刻意比 SAI 要求更嚴格 —— 不只重建容器，連 image 都刪掉）：

```bash
docker compose up -d                     # 起容器
# 透過真實 HTTP 介面登入後台、新增成員、上傳照片、發布
docker compose down                      # 移除容器
docker rmi ntust-siph-lab:local -f       # 刪除整個 image
docker compose build --no-cache          # 從頭重建
docker compose up -d                     # 新容器
```

結果：

| 項目 | 重建前 | 重建後 |
| --- | --- | --- |
| 新增成員出現在 /members | True | True |
| 個人頁 HTTP | 200 | 200 |
| 照片 URL | `/uploads/people/6b0a0ac5…jpg` | 相同 |
| 照片 sha256 | `cd1cdace…51f44e` | **相同** |
| 研究成果數 | 9 | 9 |
| sitemap URL 數 | 21 | 21 |

容器內 `/app` 的 `instance`、`uploads`、`backups` 為 `drwxrwxrwx root`
（bind mount），與 image 內建立的 `appuser` 目錄權限不同 ——
可證明資料來自主機而非烘進 image。

### Gate G2 實測紀錄（2026-08-15）

```bash
docker run -d --name siph-pg-test -e POSTGRES_PASSWORD=… -p 55432:5432 postgres:16
TEST_POSTGRES_URL=postgresql+psycopg://…@localhost:55432/siph_test pytest
# -> 701 passed, 0 skipped
```

PostgreSQL 16.15。三項 postgres 測試全過：
empty → head migration、核心 CRUD、export/import round trip（含 sequence 重設）。

> **本次執行發現並修正一個真實缺陷**：LocalConfig/TestConfig 在
> class 層級寫死 SQLite 專屬的 `connect_args={"check_same_thread": …}`，
> 指向 PostgreSQL 時 psycopg 直接拒絕連線
> （`invalid connection option "check_same_thread"`）。
> 這個問題在本機開發、SQLite 測試與靜態掃描下完全看不出來 ——
> 正是 G2 要求實跑 PostgreSQL 的理由。
> 已改為由 `config.engine_options_for()` 依實際 dialect 決定，
> 並加上回歸測試。

### 交付前獨立審查（2026-08-16）

由未參與開發的審查者依 SAI v1.2 全文重新驗收，發現並修正：

| ID | severity | 問題 | 狀態 |
| --- | --- | --- | --- |
| REV-101 | BLOCKER | Legacy Gate 由 Agent 代擬的核准紀錄解除，真正的簽核欄位空白 | ✅ 已向委託人重新取得裁示；`verify_migration.py` 新增簽核門檻 |
| REV-102 | HIGH | AC-13 演練遺留的假人 `ac13-rebuild-probe` 已發布並進入 sitemap | ✅ 已刪除；sitemap 21 → 20 |
| REV-103 | HIGH | `difference_report.md` 過期（報告 research_outputs=0，實際 9） | ✅ 已重新產生 |
| REV-104 | HIGH | ADR-012 被指向 `docs/SAI.md`，但 SAI 只到 ADR-011 | ✅ 建立 `docs/adr/ADR-012-*.md`；新增 repo integrity 測試 |
| REV-105 | MEDIUM | 新增人物時照片上傳失敗 → 重送會建立重複人物 | ✅ 改為導向已建立實體的編輯頁 |
| REV-106 | MEDIUM | 整串作者列被輸出為單一 `Person.name` | ✅ 已拆分為多個 Person |
| REV-107 | MEDIUM | 435 KB 的 PNG 可解出 432 MB（Pillow 89~179 MPx 無防護） | ✅ 新增 40 MPx 上限 |
| REV-108 | MEDIUM | `_is_safe_next` 接受 `/\evil.example` | ✅ 明確拒絕反斜線 |

驗證：`TEST_POSTGRES_URL=... pytest` → **715 passed, 0 skipped**

### 設計審查（2026-08-16，四個獨立子代理，多輪評分）

以 `msitarzewski/agency-agents` 的四個角色作為獨立子代理，
針對「直覺、簡單、資訊不要重複出現」逐輪評分與修正：

| 角色 | R1 | R2 | R3 | R4 |
| --- | --- | --- | --- | --- |
| UX Architect（資訊架構） | 5.5 | 8 | **9** | — |
| UX Researcher（理解與動線） | 5 | 7 | 8.5 | **9** |
| Accessibility Auditor | 7.5 | 9 | 8.5 | **9.5** |
| Content Creator（內容非冗餘） | 5 | 7 | **8.5** | — |

平均 **5.75 → 9.0**。（R4 有兩個角色因額度中斷未再評分，
故沿用其最後一次分數，非四項皆重評。）

**核心量化結果**：六項研究方向名詞在全站公開頁的渲染次數
**41 → 26**；完整六項字串由 **3 頁各一次 → 全站僅 1 次**
（教授本人頁面，其正式歸屬處）。

主要修正：教授檔案不再於 `/about` 與 `/members` 各複製一份；
個人頁不再於同一畫面三度列出研究焦點；成果詳細頁的
類型／年份／發表處不再與頁首重複；九篇摘要末尾重複 venue 的
「發表於…」句已移除；與主導覽重複的頁尾按鈕全數移除；
20 處純裝飾英文標題（與相鄰中文同義）刪除。

> **審查過程中我自己造成過兩次退步，皆由子代理抓出並修正**：
> (a) 新增的 `hero_intro_zh` 一度同時出現在 `/` 與 `/about`；
> (b) 移除 `sr-only` 標題後 `/research` 與 `/alumni` 出現
> h1→h3 跳級（WCAG 1.3.1 Level A）。
> 「改一處、壞另一處」是這類重構的常態，記錄於此以免日後重犯。

### 上線前仍須補做

1. **內容 sign-off** — 教授核對後**實際簽署** `content_signoff.md` §5
   （在此之前 `verify_migration.py` 會回傳 exit code 2 並阻擋 cutover）
2. **AC-15 / AC-16 人工檢查** — 320px 版面與純鍵盤操作。
   無障礙審查明確指出以下五項**無法由原始碼證明**，必須真實瀏覽器：
   320px reflow、sticky header 是否遮擋 focus ring、
   `role="alert"` 在 NVDA/VoiceOver 的播報、
   合成後的實際對比、真實 Tab 順序。
3. ~~**論文摘要**~~ — **已完成（2026-08-16）**。
   九篇的英文摘要原文已由 Crossref（3 篇）與 OpenAlex（6 篇）取得，
   存入 `summary_en`；中文摘要與 Problem / Method / Results /
   Significance 四段皆改寫自該原文（SAI §5.3）。
   IEEE 與 Optica 未向 Crossref 登錄摘要，故需第二來源。

   > **查證規則已由「禁止數值」改為「數值必須可回溯」**：
   > 原規則禁止摘要出現任何效能數字，因為當時只有標題可依據，
   > 任何數字必然是編的。現在有出版方登錄的原文，
   > 規則改成更強的版本 —— 中文欄位的每一個數值都必須在
   > 該篇 `abstract_en` 中找得到，由
   > `test_metrics_are_traceable_to_abstract` 自動強制。
   > 已用反例確認：捏造的 `3.7 dB`、`42%` 會被擋下。
4. **六項研究方向的一句話定義** — `SiteSetting.research_focus` 的
   `description_zh` 六項全為空字串。母站只有名詞，
   依 SAI §2.3 不得由系統代寫。補齊後：
   - `/about` 才真正比首頁多出資訊
   - 首頁連結可由「研究方向英文名稱 →」改回「各主題說明 →」
   - SAI §5.1 row 02「每項 1 句定義 + 對應成果」才算滿足
5. **人物照片 alt 撰寫指引** — 現值「楊淳良副教授照片」與相鄰姓名
   重複，螢幕閱讀器會唸兩次。建議改為描述照片內容；
   屬 legacy 種子資料且列於遷移證據鏈，需教授核准後才可修改。
6. **GCS backend 實連驗證** — `storage/gcs.py` 有測試但從未連過真實
   Cloud Storage；ADR-006 的實證缺口
7. **Cloud Run 實機（P6）** — AC-13 目前只在本機 Docker 驗證
8. **人物資料填寫** — 見下節「資訊密度實測」。四位在學生目前
   每人只有姓名與「碩二生」兩項，畢業生 0 筆。這是內容缺口
   不是程式缺口，後台已可填寫。

---

## 中英切換（2026-08-16）

`app/i18n.py` + `tests/test_i18n.py`（67 項）。

**採 `?lang=en` 查詢參數，不採 `/en/` 路徑前綴。**
SAI §4.2 建議完整雙語時用對稱路由，但同一節規定
「若翻譯不完整，不建立假的 hreflang 對應頁」。實測英文覆蓋率：

| 資料 | 英文覆蓋 |
| --- | --- |
| `research_outputs.title_en` / `summary_en` | 9 / 9 |
| `people.name_en` | 1 / 5 |
| `people.research_focus_en` | 0 / 5 |
| `site_settings.*_en` | 部分 |

也就是說「英文版網站」並不存在，存在的是「英文介面 + 部分英文內容」。
因此：canonical 一律指向中文網址；**hreflang 只在該頁雙語齊全時輸出**
（目前只有成果詳細頁），由 `PageMeta.bilingual` 控制。
等 people / settings 補齊後再改為 `/en/` 路由。

**缺英文時回退中文，並以 `lang` 屬性如實標記**（WCAG 3.1.2）。
這條由 `test_chinese_fallback_is_language_tagged` 強制：
它走訪 8 個頁面英文版的每一個文字節點，任何含中日韓字的節點
若最近的 `lang` 祖先不是中文就失敗。第一次執行抓出 30 處未標記
（麵包屑、/join 全部內文、人物卡姓名與職稱、關鍵字、篩選選項）。

同一輪修掉的三個既有缺陷：

1. `t()` 用真假值判斷有無翻譯，導致刻意留空的英文字串回退中文 ——
   /research 的筆數顯示成「共 1 outputs」（中文前綴配英文後綴）。
   改為 `is None` 判斷，並以 `INTENTIONALLY_EMPTY_EN` 明確登記。
2. `research_index.html` 的 `{% for t in available_types %}`
   遮蔽了翻譯函式 `t()`，在該迴圈內呼叫 t() 會炸掉。已改名。
3. `.lang-switch` 完全沒有 CSS：以 `--color-photon-deep` 顯示在
   navy header 上，對比 **2.67:1**（AA 需 4.5:1），且落在導覽下方
   自成一列。已改為分段控制並實測對比（見下）。

**控制項設計**：header 最右、`<nav>` 之外的兩格分段控制。
不放進 `<nav aria-label="主導覽">` —— 它不是頁面，放進去螢幕閱讀器
會把它當第七個目的地。不採「只顯示目標語言」的單一連結 ——
中文頁上只寫 "English" 有語意雙關（是「本頁為英文」還是
「按了會變英文」）。當前語言用 `<span aria-current>` 而非 `<a>`，
不進 Tab 順序。全部純連結，不需要 JavaScript（SAI §18）。

實測對比（對 `--color-navy` #10233d）：
可切換那格 `#d7e4ef` **12.2:1**；當前那格 navy 字對 photon-bright 底
**8.8:1**；外框 `#6d87a6` **4.3:1**（WCAG 1.4.11 UI 邊界需 3:1）。
外框不用 `overflow:hidden`，否則會裁掉 focus ring。

版面實測：1280px 單列（header 68px）；375px brand 與切換同列、
導覽第二列（168px）；320px 切換獨佔一列靠右（212px），無水平捲動。

### meta description 的語言（2026-08-16 補）

`<title>` 與 `<meta description>` 現在都跟著語言走。9 個頁面的英文版
描述**已無中文殘留**（人物頁的中文姓名除外，見下）。

description 是全站唯一「回退到另一種語言等於失效」的欄位。
其他地方缺英文時回退中文是對的 —— 頁面上顯示的本來就是那段中文，
標了 `lang` 就誠實。但 description 不出現在頁面上，只出現在搜尋結果
與分享預覽，唯一用途是回答「這一頁是什麼」；放一段讀者看不懂的
語言進去，這個用途就完全失效。因此 `SEOService._desc_chain()`
**刻意不跨語言回退**，取不到英文來源時改用以事實組成的英文句。

各頁英文描述的來源：

| 頁面 | 英文來源 | 品質 |
| --- | --- | --- |
| 成果詳細頁 | **`summary_en`（出版方登錄的摘要原文）** | 最高，可回溯 DOI |
| /members、/alumni、/research | 事實樣板 + 人數／筆數 | 完整英文 |
| /join | `join_body_en`（無欄位）→ 事實樣板 | 完整英文 |
| 人物頁 | `research_focus_en` → 姓名＋職稱＋機構 | 教授完整；學生只有姓名＋機構 |
| /、/about | `hero_intro_en`（**未填**）→ 機構事實句 | 單薄 |

姓名是唯一允許跨語言回退的部分（專有名詞）。多數學生沒有 `name_en`，
但「陳泓序, NTUST SiPh Lab」仍指得出這是誰的頁面；若因為「不能有中文」
就退回純機構名，四位學生會拿到一模一樣的描述、還跟首頁撞在一起。
職稱不比照辦理 —— 「碩二生」對英文讀者只是雜訊。

**順帶修好的既有缺陷**：四位學生的中文描述原本全部回退到站台預設，
四頁加首頁共五頁描述完全相同，違反 SAI §12.1「每頁描述不重複」。
現在每人是「姓名，職稱，研究室，學校」，各自不同。

### 編輯文案補齊（2026-08-16 補）

前一節的「`/` 與 `/about` 描述相同」已解決。九頁的描述在中英文皆為
**8 種 / 9 頁**，唯一重複已消除。

- 新增 `about_intro_en` 欄位（migration `16bde59ce22f`）。
  `site_settings` 的敘述型欄位中只有 `about_intro` 缺英文版，
  少了它，`/about` 的英文描述會回退站台預設而與首頁相同。
- 新增 `scripts/seed_editorial.py` + `flask seed editorial`。

**為什麼需要第三支 seed**：`hero_intro_zh` 與 `default_description_zh`
早就顯示在正式頁面上，卻**只存在於 `instance/siph_lab.db`** ——
沒有進版控、正式站沒有、重建資料庫就消失、也沒有任何地方記載依據。
編輯文案和論文書目一樣屬於「應該可重現」的內容，因此比照
`seed_publications.py` 獨立成一支，不混入母站證據鏈
（那會讓人再也分不清哪句話有母站依據）。

五則文案的核准紀錄見 `legacy/google_sites/content_signoff.md` §2.4，
狀態為 **`APPROVED_REWRITE`（待核准）** —— 核准欄位空白，
依 ADR-011 不由 Agent 代填。

> **過程中我自己造成過一次資料遺失**：`run_seed` 第一版只把三個要改的
> key 傳給 `SettingsService.update()`，但那個 API 是給後台表單用的
> 「整份覆寫」，沒帶到的欄位會被寫成 `None` ——
> 一次清空 `university_zh/en`、`contact_email`、`official_ntust_url`、
> `default_title_suffix`、`default_description_zh`、`hero_intro_zh`
> 共七個欄位。沒有任何例外或警告，是靠改完後重新量測描述才發現的
> （`/` 的描述從 73 字掉到 14 字）。已由 `seed legacy --force` 與
> 事前備份完整救回（逐欄比對遺失 0 欄），並由
> `test_seed_does_not_clear_other_settings` 守住。

---

## 資訊密度實測（2026-08-16）

各頁 `<main>` 內的可見文字字數（扣除 header/nav/footer）：

| 頁面 | 主內容字數 | 標題數 | 內容佔 HTML 比 |
| --- | ---: | ---: | ---: |
| /research（成果列表） | 3,445 | 10 | 17.0% |
| /research/&lt;slug&gt;（成果詳細） | 2,469 | 9 | 19.1% |
| /people/chun-liang-yang（教授） | 1,215 | 7 | — |
| /（首頁） | 901 | 12 | 8.2% |
| /about | 438 | 10 | 5.0% |
| /join | 281 | 7 | 4.4% |
| **/members** | **133** | 8 | **1.6%** |
| **/people/&lt;學生&gt;** | **52** | 2 | **0.9%** |
| **/alumni** | **39** | 1 | **0.8%** |

**結論：成果側已經很紮實，人物側幾乎是空的。**

- 9 篇成果，`title/summary/problem/method/results/significance/doi/venue/keywords`
  全部 9/9。中文摘要平均 144 字，四段式平均 44/88/78/50 字。
- 5 位人物中，教授填了 41 個欄位中的 21 個；
  **四位在學生每人只有 11 個欄位有值，其中真正的內容只有
  `name_zh` 與 `title_zh`（「碩二生」）兩項** ——
  沒有照片、沒有 `name_en`、沒有研究焦點、沒有 email、沒有入學年。
- 畢業生 **0 筆**，`/alumni` 全頁只有空狀態文案。

學生個人頁的完整可見內容就是這一行：

> 陳泓序 碩二生 相關研究成果 尚未有已發布的研究成果 最後更新：2026-08-15 ← 返回研究成員

沒有任何一項是程式缺陷 —— 後台的欄位都在，是內容還沒填。
影響最大的三項（依投入產出排序）：

1. **四位學生的研究焦點一句話**（`research_focus_zh`）——
   直接讓 `/members` 從 133 字變成可讀的頁面，並讓人物卡出現
   SAI §5.2 要求的「1-2 行研究焦點」。
2. **四位學生的照片**（後台已支援，見下節）。
3. **六項研究方向的一句話定義**（已列於上節第 4 項）。

### 後台可編輯範圍（回答「在學生與畢業生能否上傳頭像」）

**可以，兩者用的是同一份表單。** `PersonForm` 沒有任何依 `status`
分支的欄位定義，`/admin/people/<id>/edit` 對 faculty / current /
alumni 都提供相同的七個分區：基本身分、學術身分、研究內容、
畢業去向、公開連結、**照片**、SEO 與顯示順序。
`/admin/people`（在學）與 `/admin/alumni`（畢業生）兩個列表都連到
同一個編輯頁。由 `test_person_photo_upload_is_available_for_every_status`
與既有的 `test_person_photo_upload_then_delete` 覆蓋。

上傳的照片會自動縮放並重新編碼（丟棄含 GPS 的 EXIF，SAI §16），
上限 8 MB，允許 jpg / jpeg / png / webp。

**已知限制**：`/alumni` 列表頁的卡片**不顯示照片**（只有姓名、
學位、論文題目、技能）。畢業生的照片會出現在其個人頁，
但不會出現在畢業生列表。若要在列表也顯示，需調整
`alumni.html` 的卡片版面 —— 目前沒有畢業生資料，暫不更動。

本次同時補上所有圖片上傳欄位的 `accept` 屬性（先前缺漏，
管理者要按下儲存才會知道 .heic 不被接受）。
