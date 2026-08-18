# NTUST SiPh Lab 決策紀錄（碼層）

本檔保存**無法只從語法還原**、且後續維護不得任意改寫的產品／安全決策。
程式中的 `NOTE(NOTE-NNN):` 必須能在此找到同號條目；若行為改變，需同步更新
決策、測試與引用處，**禁止留下失效 reference**。

## 本檔與 SAI／ADR 的分層

三層各有職責，**不得互相複製**：

| 層級 | 位置 | 內容 | 誰維護 |
|---|---|---|---|
| 規格層 | `docs/SAI.md`（+ PDF） | ADR-001 ~ ADR-011 | **外部交付規格，本 repo 不得改寫** |
| 決策層 | `docs/adr/` | ADR-012 起的新決策 | 本 repo（依 SAI §25「未來若新增決策必須另開 ADR」） |
| 碼層 | **本檔** | 實作層的不變量與取捨 | 本 repo |

> **為什麼 ADR-001~011 不搬進本檔**：SAI 是外部交付的規格來源，
> 其決策表僅到 ADR-011 且不由本 repo 維護（見 `docs/adr/ADR-012` 開頭說明）。
> 把它們複製進來會製造第二個真相來源，日後 SAI 更新時必然漂移。
> 需要引用時直接寫 `ADR-00N（見 docs/SAI.md 決策摘要表）`。

## 登記狀態

以下 NOTE 為 **2026-08-18 追認登記**：決策與理由早已寫在程式碼的
「特殊機制 / 已知限制與禁止事項」區塊中（實作日期 2026-08-14 ~ 08-16），
本次只是給它們編號、集中索引、並補上驗證指標。
**追認不等於重新決策**，內容一律以原始碼中既有的敘述為準，未經潤飾擴充。

---

## NOTE-001：`?next=` 必須另外阻擋反斜線

- 追認登記：2026-08-18（實作於 2026-08-16，交付前審查 REV-108）
- 適用範圍：`app/blueprints/auth/routes.py` 的 `_is_safe_next()`
- 決策：`?next=` 目標除了「必須以單一 `/` 開頭、不得以 `//` 開頭、
  不得含 scheme 或 netloc」之外，**只要含有反斜線就一律拒絕**。
- 原因：依 WHATWG URL 規範，在 http/https 這類 special scheme 下，
  URL 解析器把 `\` 視同 `/`，因此 `/\evil.example` 在瀏覽器眼中等於
  `//evil.example`（protocol-relative，會離站）。但 Python 的 `urlparse`
  **不做**這個轉換，`netloc` 會是空字串——這個認知落差就是繞過點。
- 維護邊界：該字串目前碰巧不可利用，因為 Werkzeug 會把 Location 百分比
  編碼成 `%5C`。**但那是下游函式庫剛好救了我們，不是這個守衛函式做對了**；
  換個 Werkzeug 版本或關掉 `autocorrect_location_header` 就會變成真的開放轉址。
  安全檢查不得依賴呼叫端以外的偶然行為，**不得以「反正 Werkzeug 會處理」為由簡化**。
- 驗證：`tests/test_auth.py::test_unsafe_next_targets_rejected`（6 個離站樣本）
  與 `::test_safe_next_targets_accepted`（3 個正常路徑，防矯枉過正）。

## NOTE-002：登入失敗訊息不得區分「帳號不存在」與「密碼錯誤」

- 追認登記：2026-08-18（實作於 2026-08-14）
- 適用範圍：`app/blueprints/auth/routes.py` 登入流程與其 flash 訊息
- 決策：兩種失敗必須回**完全相同**的訊息。
- 原因：若可區分，攻擊者能先列舉出存在的帳號，再把猜測算力集中在密碼上。
- 維護邊界：修改文案時必須同時改兩處，或維持共用同一常數。
- 驗證：`tests/test_auth.py::test_ac02_failed_login_writes_audit_log`
  （稽核面）；訊息一致性目前**無專屬測試**，見文末「待補測試」。

## NOTE-003：照片上傳失敗不得把使用者送回新增表單

- 追認登記：2026-08-18（實作於 2026-08-16）
- 適用範圍：`app/blueprints/admin/routes.py` 的 `person_new()`（約 253-266 行）
  與 `research_new()`（約 511 行）
- 決策：人物／成果建立成功並 commit 之後，主圖上傳若失敗，
  一律 **redirect 到該筆的編輯頁**並說明「哪一半成功了」，
  **不得** re-render 新增表單。
- 原因：送回新增表單會讓管理員以為整筆都失敗而重新送出，結果建立第二筆；
  slug 會被自動去重成 `-2`，產生難以察覺的重複實體。
- 維護邊界：任何「先 commit 主體、再處理附屬資源」的流程都適用此模式；
  新增類似流程時必須比照，不得只在這兩處成立。
- 驗證：`tests/test_admin.py`、`tests/test_media.py`（上傳失敗路徑）。

## NOTE-004：Admin 權限用 `before_request` 全域套用

- 追認登記：2026-08-18（實作於 2026-08-14）
- 適用範圍：`app/blueprints/admin/` 整個 blueprint
- 決策：以 `before_request` 對整個 blueprint 套用 `login_required`，
  **不逐個 route 加裝飾器**。
- 原因：漏加裝飾器是最常見的權限漏洞。「預設全部要登入」讓遺漏**不可能發生**，
  而不是靠 code review 抓。
- 維護邊界：新增任何 route 都自動受保護；若某 route 需要公開，
  必須顯式豁免並在此登記——**不得把保護改回逐 route 套用**。
- 驗證：`tests/test_auth.py::test_ac01_unauthenticated_admin_redirects_to_login`
  （參數化涵蓋多個 admin 路徑，AC-01）。

## NOTE-005：公開頁的任何 GET 都不得產生資料庫寫入

- 追認登記：2026-08-18（實作於 2026-08-14）
- 適用範圍：`app/blueprints/public/routes.py` 全部路由
- 決策：公開頁一律唯讀。唯一例外是 `SiteSetting.get()` 首次建立預設值。
- 原因：公開頁面對爬蟲與匿名流量，任何 GET 寫入都會被放大成寫入風暴，
  且讓「唯讀副本 / 快取」這類擴充方式失效。
- 維護邊界：新增公開路由時不得引入寫入；需要計數等功能必須另案設計。
- 驗證：`tests/test_repo_integrity.py`（結構性檢查）。
  **無直接的「GET 不寫入」測試**，見文末「待補測試」。

## NOTE-006：`/uploads/<path>` 只在 `STORAGE_BACKEND=local` 時註冊

- 追認登記：2026-08-18（實作於 2026-08-15）
- 適用範圍：`app/blueprints/public/routes.py` 的 uploads 路由（約 62-67、464 行）
- 決策：僅本機開發（local backend）註冊此路由；production 使用 GCS，
  媒體由 object storage 直接服務。
- 原因：若在 production 誤留此路由，等同讓 Cloud Run 承擔靜態檔案流量，
  成本與延遲都不必要（SAI §21.4）。
- 驗證：`tests/test_storage_backends.py`、`tests/test_config.py`。

## NOTE-007：稽核 IP 不提供「固定預設 salt」這個選項

- 追認登記：2026-08-18（實作於 2026-08-14）
- 適用範圍：`app/models/audit_log.py` 的 IP 處理（約 55-63 行）
- 決策：只有兩種模式——未提供 `AUDIT_IP_SALT`（預設）就**完全不記錄 IP**；
  提供高熵 salt 則存 SHA-256 雜湊前 32 字元。**不提供固定預設 salt**。
- 原因：固定預設 salt 會產生**可被完整反查、卻讓人誤以為安全**的假去識別化——
  比不做去識別化更危險，因為它會讓人停止警惕。
- 維護邊界：此表只增不改；禁止提供 UI 讓管理員編輯或刪除稽核紀錄。
- 驗證：`tests/test_schema.py::test_audit_action_check_constraint`、
  `::test_audit_log_survives_admin_deletion`。
  **salt 模式本身無專屬測試**，見文末「待補測試」。

## NOTE-008：稽核寫入失敗不得讓使用者的正常操作失敗

- 追認登記：2026-08-18（實作於 2026-08-14）
- 適用範圍：`app/models/audit_log.py` 的 `write()`
- 決策：`summary` 過長時**截斷並附省略號**，不拋例外。
- 原因：稽核是旁路關注點；讓它反過來擋掉使用者的正常操作，
  是把可用性賠給了觀測性。
- 維護邊界：這是**刻意不擋**的失敗，不是遺漏的錯誤處理——
  看到「寫入失敗卻沒有拋例外」時不要「修好」它。
- 驗證：`tests/test_schema.py`（欄位約束）。

## NOTE-009：model 的 import 順序與「不得刪除未使用 import」

- 追認登記：2026-08-18（實作於 2026-08-14）
- 適用範圍：`app/models/__init__.py`
- 決策：import 順序固定為 `admin_user -> person -> research_output`，
  且**禁止為了消除 unused import 警告而刪除任何一行 import**。
- 原因：兩者互相參照，先載入 `person` 才能讓 relationship 字串在 mapper
  設定時解析成功。而刪掉看似沒用的 import，會讓對應資料表從 Alembic
  metadata 中消失——`flask db migrate` 從此**靜默**漏掉那張表，
  直到 production 查詢失敗才會發現。
- 維護邊界：新增 model 必須同時加 import 與 `__all__`；
  統一以 `noqa: F401` 標註，不要改用 linter 豁免設定繞過。
- 驗證：`tests/test_schema.py::test_all_core_tables_present`、
  `::test_models_are_all_registered_in_metadata`。

---

## 待補測試（本次追認時發現的缺口）

以下決策**目前沒有對應測試**，代表它們可以被無聲改掉：

| NOTE | 缺什麼 |
|---|---|
| NOTE-002 | 「兩種登入失敗訊息完全相同」無斷言 |
| NOTE-005 | 「公開頁 GET 不產生寫入」無斷言 |
| NOTE-007 | 「未設 salt 時 IP 欄為 null」「不接受固定弱 salt」無斷言 |

建議一併在 `tests/test_repo_integrity.py` 加入制度性檢查：
程式中出現的每個 `NOTE(NOTE-NNN):` 都必須在本檔找得到同號條目，
且本檔提到的每個測試路徑都必須存在——把「禁止失效引用」變成 CI 擋得住的規則。
