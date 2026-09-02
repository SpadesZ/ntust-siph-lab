# 檔頭註解（header comment）與 NOTE 制度符合度稽核

- 稽核日期：2026-08-18（含當日修補）
- 稽核範圍：`ntust-siph-lab` 全部程式碼共 **121 檔**
  （`.py` 87、`.html` 29、`.css` 3、`.yml` 1、`.yaml` 1；
  已排除 `.venv`、`.git`、`__pycache__`、`.pytest_cache`、`legacy/`）
  其中 **120 檔受版控**；`scripts/generate_valuation_pdf.py` 由 `.gitignore`
  刻意排除（含請款資料，repo 未來可能交付校方）。
- 比對基準：**十欄檔頭規範**（見 §1）。

---

## 0. 結論摘要

| 項目 | 稽核當下 | 修補後 |
|---|---|---|
| 完全無檔頭 | 3 | **0** |
| 十欄全備 | 0 | 4 |
| `功能說明` 欄 | 0 | 4 |
| `docs/NOTES.md` | 不存在 | **已建立，9 則 NOTE** |
| 程式中 `NOTE(NOTE-NNN):` 標記 | 0 | **9（雙向閉環，無孤兒）** |
| 檔案層級引用失效 | 0 | 0 |
| **函式層級引用失效** | **23 / 33（70%）** | **0 / 56** |
| NOTE 有可執行斷言保護 | — | **9 / 9** |
| CI 是否擋得住上述問題 | 否 | **是（新增 3 項制度檢查）** |
| 測試 | 818 passed | **829 passed, 3 skipped** |

一句話：**檔頭覆蓋率高但欄位不齊；NOTE 制度原本整套缺席，本次已建立；
最嚴重的問題是本來沒被發現的「函式層級失效引用」。**

---

## 1. 規範基準（十欄）

```
# <專案名> source maintenance contract
# 上下游:      誰呼叫本檔、讀寫哪些資料、結果流向何處
# 檔案路徑:
# 產生時間:    （本專案用「建立日期」＋「最後重大修改」，等價）
# 版本:
# 功能說明:    這支程式實際在做什麼（白話，看完不必讀碼就懂）
# 模組定位:    在架構中是什麼、以及「不是」什麼
# 主要責任:    編號列舉，點到函式名
# 維護提醒:    （本專案用「維護契約」，等價）禁令與刻意的取捨
# 驗證方式:    可直接貼上執行的指令
# ------------------------------------------------------------
```

`功能說明` 為 2026-08-18 新增。本專案的欄位名是規範的**等價變體**，
稽核時按語意認列，未因換名而判為缺件：

| 規範欄位 | 本專案寫法 |
|---|---|
| `產生時間` | `建立日期` ＋ `最後重大修改`（更好，區分建立與修改） |
| `主要責任` | `責任邊界`（多半併入 `模組定位與責任邊界`） |
| `維護提醒` | `維護契約` |

本專案另外**多做**了三件值得保留的事：
`輸入 -> 處理 -> 輸出 Pipeline`、`主要匯出`／`依賴套件`、
以及在檔頭直接引用 `SAI §章節` 與 `ADR-NNN`。

---

## 2. 逐欄覆蓋率（121 檔）

| 規範欄位 | 覆蓋率 | 判定 |
|---|---:|---|
| 檔案路徑 | 100% | 良好 |
| 時間 | 100% | 良好，但內容失準（見 §5） |
| 版本 | 100% | 形式有、資訊量為零（見 §5） |
| 上下游 | 76% | 尚可 |
| 驗證方式 | 72% | 形式尚可，**內容有嚴重問題**（見 §3） |
| 模組定位 | 55% | 不足 |
| 主要責任 | 48% | 不足 |
| 維護提醒 | 41% | 最弱 |
| **功能說明** | **3%** | **新增欄位，幾乎全缺** |

依目錄（平均命中，滿分 10）：
`deploy/` 1.0 ＜ `migrations/` 6.7 ＜ `tests/` 4.8 ＜ `scripts/` 5.9
＜ `app/` 6.3 ＜ 根目錄 7.7。

---

## 3. 最嚴重：函式層級的失效引用（20 處）

**這是上一版稽核漏掉的問題。** 上一版檢查了「`驗證方式` 提到的 25 個測試**檔案**
是否存在」，結論是「零失效引用」——但那只驗到檔案層級。
實際以 `檔案::函式` 為單位重驗後：**32 個引用中有 22 個指向不存在的測試函式**。

規範明訂「禁止留下失效 reference」。這類引用比沒有引用更糟：
它讓維護者以為某個行為有測試保護，實際上沒有。

**已全部修正（23 處）。** 每一處都逐一判斷是「測試改名」還是「測試根本沒寫」，
未使用批次取代——那只會把失效引用換成指向錯誤測試。

三處屬於指錯檔案或無測試，處理方式不同：

| 檔案 | 原引用 | 處理 |
|---|---|---|
| `app/extensions.py` | `test_db_portability.py::test_sqlite_foreign_keys_enforced` | 連**檔案**都指錯，實際在 `test_schema.py` |
| `app/templates/admin/_macros.html` | `test_admin.py::test_form_fields_have_labels` | 實際在 `test_a11y.py::test_ac16_admin_forms_have_associated_labels` |
| `app/static/css/tokens.css` | `test_a11y.py::test_contrast_tokens_documented` | **對比值根本沒有自動測試**，改列 3 個真實的 a11y 測試，並明寫「對比僅靠人工複驗」 |

另有 2 處（`admin/people_list.html`、`admin/research_list.html`）宣稱驗證
「列表篩選」，但 admin 列表篩選確實沒有測試。已改為指向真實的
`test_admin_pages_render`，並加註「篩選行為沒有自動測試，須人工複驗」——
**不假裝有覆蓋**。

> **`.txt` 的教訓**：本次修完 20 處後，裝進 CI 的檢查器立刻又抓到第 21 處——
> `app/templates/public/robots.txt` 指向不存在的 `test_robots_disallows_admin`
> （實際為 `test_robots_disallows_admin_and_points_to_sitemap`）。
> 稽核腳本漏掉它，是因為掃描副檔名沒有納入 `.txt`，而 repo 既有的
> `_SCANNED_SUFFIXES` 有。**這正是「把規則交給 CI，而不是交給稽核者的細心」的理由。**

原始失效清單（保留作為紀錄）：

| 引用來源 | 當時指向的不存在測試 |
|---|---|
| `app/models/audit_log.py:77` | `tests/test_auth.py::test_login_writes_audit_log` |
| `app/models/redirect.py:70` | `tests/test_research.py::test_slug_change_creates_redirect` |
| `app/services/publish_validator.py:79` | `tests/test_people.py::test_publish_requires_photo_alt` |
| `app/static/css/tokens.css:68` | `tests/test_a11y.py::test_contrast_tokens_documented` |
| `app/storage/local.py:65` | `tests/test_storage_backends.py::test_path_traversal_rejected` |
| `app/templates/admin/_macros.html:41` | `tests/test_admin.py::test_form_fields_have_labels` |
| `app/templates/admin/alumni_list.html:30` | `tests/test_admin.py::test_alumni_list_renders` |
| `app/templates/admin/change_password.html:32` | `tests/test_auth.py::test_change_password` |
| `app/templates/admin/people_list.html:31` | `tests/test_admin.py::test_people_list_filters` |
| `app/templates/admin/research_list.html:29` | `tests/test_admin.py::test_research_list_filters` |
| `app/templates/admin/system.html:32` | `tests/test_admin.py::test_system_page_renders` |
| `app/templates/public/_person_card.html:40` | `tests/test_people.py::test_member_card_fields` |
| `app/templates/public/about.html:36` | `tests/test_legacy_migration.py::test_professor_fields_visible` |
| `app/templates/public/alumni.html:33` | `tests/test_people.py::test_alumni_grouped_by_year` |
| `app/templates/public/home.html:49` | `tests/test_seo.py::test_home_has_single_h1` |
| `app/templates/public/join.html:32` | `tests/test_seo.py::test_join_page_renders` |
| `app/templates/public/members.html:30` | `tests/test_people.py::test_members_page_lists_published_only` |
| `app/utils/dates.py:70` | `tests/test_seo.py::test_sitemap_lastmod_format` |
| `app/utils/validators.py:70` | `tests/test_validators.py::test_dangerous_scheme_rejected` |
| `scripts/legacy_baseline.py:234` | `tests/test_legacy_migration.py::test_lab_name_migrated` |

（另加 `app/templates/public/robots.txt:26`、`app/models/__init__.py:59`、
`app/extensions.py:80`，共 23 處，全部已修。）

---

## 4. NOTE 制度：已建立

原本 `docs/NOTES.md` 不存在、程式中 `NOTE(NOTE-NNN):` 標記 0 個。
本次建立 `docs/NOTES.md`，登記 9 則碼層決策並在程式中打上標記，
雙向閉環驗證通過（9 條目 ↔ 9 標記，無孤兒條目、無孤兒標記）。

### 為什麼 ADR-001~011 沒有搬進 NOTES.md

上一版稽核建議「把只有一行摘要的 ADR-001~011 補成完整決策記錄」，
**該建議已撤回**。查證後發現這是設計使然，不是缺陷：

- `docs/SAI.md` 是**外部交付的規格來源**，其決策表僅到 ADR-011，
  且該 PDF 不由本 repo 維護、**不應被改寫**。
- SAI §25 明訂「未來若新增決策必須另開 ADR」，
  `docs/adr/ADR-012` 開頭已把這個分層講清楚。

把 ADR-001~011 複製進本 repo 會製造**第二個真相來源**，日後 SAI 更新必然漂移。
因此三層分工為：SAI（ADR-001~011，外部規格）→ `docs/adr/`（ADR-012+，repo 決策）
→ `docs/NOTES.md`（碼層不變量）。

> **另一處更正**：上一版說 ADR-001~011「沒有原因」。這句講過頭了——
> SAI 的決策摘要表**有「理由」欄**，只是一行。準確的說法是
> 「有一行理由，但沒有反例、沒有驗證指標」。

### 已登記的 9 則 NOTE

| NOTE | 主題 |
|---|---|
| NOTE-001 | `?next=` 必須另外阻擋反斜線（WHATWG vs urlparse 認知落差） |
| NOTE-002 | 登入失敗訊息不得區分帳號不存在與密碼錯誤 |
| NOTE-003 | 照片上傳失敗不得把使用者送回新增表單（會造成重複實體） |
| NOTE-004 | Admin 權限用 `before_request` 全域套用，不逐 route 加裝飾器 |
| NOTE-005 | 公開頁任何 GET 不得產生資料庫寫入 |
| NOTE-006 | `/uploads/<path>` 只在 `STORAGE_BACKEND=local` 時註冊 |
| NOTE-007 | 稽核 IP 不提供「固定預設 salt」這個選項 |
| NOTE-008 | 稽核寫入失敗不得讓使用者的正常操作失敗 |
| NOTE-009 | model import 順序與「不得刪除未使用 import」 |

全部標示為**追認登記**：決策與理由早已寫在程式碼的「特殊機制 /
已知限制與禁止事項」區塊，本次只是編號、集中索引、補上驗證指標，
內容未經潤飾擴充。

NOTES.md 文末另列出 **3 則沒有對應測試**的 NOTE（002、005、007），
代表這些決策目前可以被無聲改掉。

---

## 5. 其他缺口

**檔頭日期失準**：以 git 實際最後變更日期比對，117 個可比對檔案中
**102 個（87%）**檔頭日期落後；只有 52 檔有寫 `最後重大修改`。
（註：不可用檔案 mtime 判斷——08-17 有一批 merge/checkout 會一次刷新 mtime。）

**`版本：` 形同虛設**：117 個有版本欄的檔案**全部都是 v1.0**。
對照母體 roothinks 的 `v3.6`（維護提醒逐版累積），本專案的版本欄不承載資訊。

**模板與測試缺後三欄**：29 個 `.html` 多停在 5/10；
28 個 `tests/` 缺維護提醒(27)、主要責任(26)、模組定位(24)。

---

## 6. 本次已完成的修補

1. **`docs/NOTES.md`** 新建，9 則 NOTE，雙向閉環。
2. **3 個無檔頭檔案補齊**（十欄格式）：
   - `scripts/generate_valuation_pdf.py`（324 行，含 gitignore 理由與兩處硬編碼 Windows 路徑警告）
   - 初始 schema migration（`aee291c6966c`，261 行，7 張核心表）
   - 雙語欄位 migration（`16bde59ce22f`，保留原有的優良理由敘述）
   - 已驗證兩個 migration 的 revision id 與 Alembic docstring 未受影響。
3. **`app/models/__init__.py`** 升級為十欄樣板（拆開模組定位／主要責任、
   補功能說明、修正失效引用、登記 NOTE-009），版本 v1.0 → v1.1。
4. **23 處函式層級失效引用全部修正**（見 §3）。
5. **`tests/test_repo_integrity.py` 新增 3 項制度性檢查**（v1.0 → v1.1）：
   - `test_no_dangling_test_function_references`：
     `tests/x.py::test_y` 必須精確到**函式層級**存在
   - `test_every_note_marker_has_an_entry`：
     程式中每個 `NOTE(NOTE-NNN)` 必須在 `docs/NOTES.md` 有同號條目
   - `test_every_note_entry_is_referenced_in_code`：
     反方向——NOTES.md 的條目必須有程式碼引用點，避免決策與實作脫節

   三項都做過**變異測試**確認不是空轉：植入 `NOTE-999` 孤兒標記與
   `NOTE-998` 無引用條目，兩項檢查都如預期失敗，移除後恢復通過。
   稽核報告本身以 `header-note-audit-*` 前綴豁免，因為它刻意記錄壞掉的引用作為歷史證據。
6. **`tests/test_note_invariants.py` 新建**（8 條）：替 NOTE-005／007／008
   補上原本沒有的行為斷言，見 §7。
7. 全測試通過：**829 passed, 3 skipped**（skip 為未設 `TEST_POSTGRES_URL`）。

---

## 7. 建議後續順序

| 順位 | 工作 | 理由 |
|---|---|---|
| ~~1~~ | ~~處理失效引用~~ | ✅ 已完成（23 處） |
| ~~2~~ | ~~加制度性檢查~~ | ✅ 已完成（3 項，含變異測試） |
| ~~3~~ | ~~補缺測試的 NOTE~~ | ✅ 已完成（`tests/test_note_invariants.py`，8 條） |
| 4 | 全面補 `維護契約`（缺 72 檔）與 `責任邊界`（缺 65 檔） | 覆蓋率最低、價值最高的兩欄 |
| 5 | 補 `功能說明` 欄至全部檔案 | 新欄位，目前 4/121 |
| 6 | 讓 `版本` 真的遞增、補 `最後重大修改` | 目前 87% 日期失準、版本全為 v1.0 |

剩下的第 4~6 項屬於大量、低風險的機械性補件，可分批進行。

### 第 3 項的執行結果與一處更正

新增 `tests/test_note_invariants.py`（8 條），專收「跨模組、決策層級、
在既有測試檔中沒有自然歸屬」的不變量：

- **NOTE-005**：以 SQLAlchemy `before_cursor_execute` 攔截實際送出的 SQL，
  斷言暖機後的公開頁 GET 不再產生任何寫入。用攔截 SQL 而非比對資料列數，
  是因為列數比對會漏掉「寫入後又改回原值」與「寫入後 rollback」，
  兩者仍然違反本決策。
- **NOTE-007**（6 條）：含一條 `test_note007_salt_actually_participates_in_the_digest`
  ——**少了它，把實作改成純 `sha256(ip)` 也會讓其他測試全綠**，
  而那正是本 NOTE 要防的假去識別化。
- **NOTE-008**：超長 summary 截斷而非拋錯。

NOTE-005 的攔截器另做過控制組驗證：在同一個 context manager 內執行
`AuditLog.write()` + commit，確認確實攔到 `INSERT INTO audit_logs`，
證明該測試不是因為 listener 沒掛上而假性通過。

> **更正**：上一版報告與 NOTES.md 初版都記載「NOTE-002 訊息一致性無專屬測試」。
> **那是錯的。** `tests/test_auth.py::test_ac02_unknown_user_and_wrong_password_are_indistinguishable`
> 一直都在，而且比預期更完整——連「錯誤訊息不得出現『不存在』『查無』字樣」
> 都驗了，還特地說明為何只比對 flash 而不比對整份 HTML（表單會回填使用者
> 自己輸入的帳號，那不構成資訊洩漏）。
> **教訓：補測試前先查既有測試，不要憑稽核腳本的關鍵字比對就認定沒有。**

> 值得注意：第 1、2 項合起來的效果是——同類問題以後不需要再靠稽核發現。
> 這正是本次最有價值的產出：`test_repo_integrity.py` 在裝好後**立刻**
> 抓到一處連本次稽核腳本都漏掉的失效引用（見 §3 的 `.txt` 教訓）。
