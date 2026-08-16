# NTUST SiPh Lab — Content Sign-off Record

> **檔案路徑**：`legacy/google_sites/content_signoff.md`
> **建立日期**：2026-08-15　**版本**：v1.0
> **用途**：SAI §22.6 要求的內容核准紀錄。
> 正式 launch 的**必要條件**為 UNRESOLVED = 0，且本檔完成簽核。

---

## 1. Baseline 摘要（SAI §22.6 規定欄位）

| 欄位 | 內容 |
| --- | --- |
| Baseline snapshot 日期 | 2026-08-15（SAI 原始盤點：2026-08-14） |
| 母站來源 | <https://sites.google.com/view/ntust-siph-lab/> |
| Inventory 總筆數 | **19**（LC-001 ~ LC-019） |
| MIGRATED 數 | **18** |
| APPROVED_REWRITE 數 | **0** |
| APPROVED_REMOVE 數 | **1**（LC-019） |
| REVIEW_REQUIRED 數 | **0** |
| **UNRESOLVED 數** | **0** ✅ |
| 自動驗證結果 | AC-21 ~ AC-26 全數通過（見 `difference_report.md`） |
| 核對人 | 研究室管理者（本專案委託人） |
| 核對日期 | 2026-08-15 |

> 上述數值由 `python scripts/verify_migration.py --all` 自動產生並寫入
> `difference_report.md`。本檔的數字必須與該報告一致。

---

## 2. 管理者裁示紀錄

以下三項為 SAI 規定必須由教授／管理者裁決、**Agent 不得自行決定**的事項。

> **稽核更正（2026-08-16 交付前獨立審查）**
> 本節 §2.1、§2.2 原記載核准日期為 2026-08-15，且「理由」欄填寫的是
> 工程論證而非委託人本人的陳述 —— 該內容實際由 Agent 代擬，
> 屬 ADR-011 明確禁止的行為（Agent 不得自行決定母站內容去留）。
>
> 兩項裁示已於 **2026-08-16** 向委託人重新取得，本節現已更正為：
> 明確區分「委託人原話／委託人理由」與「審查者提供的技術補充」。
> 此後任何裁示紀錄都必須維持此分欄，不得混寫。

### 2.1 LC-019 — Google Calendar 嵌入（台灣假日日曆）

| 項目 | 內容 |
| --- | --- |
| SAI 原始狀態 | `REVIEW_REQUIRED`（SAI §2.1） |
| 規格約束 | §22.1 明定 REVIEW_REQUIRED 阻擋上線；AC-25 要求必須有明確 MIGRATED 或 APPROVED_REMOVE 決策；§23.1 禁止 Agent 自行刪除母站內容 |
| **裁示結果** | **`APPROVED_REMOVE`** |
| 核准人 | 研究室管理者（本專案委託人） |
| 核准日期 | **2026-08-16** |
| 核准依據 | 委託人於 2026-08-16 交付前審查的回覆中，就本項目明確裁示 |
| **委託人原話** | 「我認為這塊其實沒有很需要」 |
| 委託人理由 | 非研究室必要內容。 |
| 技術補充<br>（審查者提供，**非**委託人陳述） | 台灣假日日曆屬第三方通用行事曆，與 SAI §3 定義的四類訪客（潛在研究生／學術訪客／在學學生／畢業生）目標皆無關；且將是全站唯一的第三方 iframe，需為其放寬現行 CSP（目前為 `default-src 'self'`、`frame-ancestors 'none'`）。 |
| 影響 | 新站不設置此嵌入。母站原始內容已保存於 `source_snapshot.md`，可隨時還原。 |
| 記錄位置 | `scripts/legacy_baseline.py` 的 LC-019 項目 `approval` 欄位 |

### 2.2 四位碩二生的發布策略（LC-015 ~ LC-018）

| 項目 | 內容 |
| --- | --- |
| 規格衝突 | §15.1 發布門檻要求 research focus 為必填；§2.3／§23.1 禁止 Agent 猜測填入研究方向；AC-23 要求四人姓名必須能在新站被找到 |
| **裁示結果** | **發布，缺漏欄位列入待補** |
| 核准人 | 研究室管理者（本專案委託人） |
| 核准日期 | **2026-08-16** |
| 核准依據 | 委託人於 2026-08-16 交付前審查的回覆中明確裁示 |
| **委託人原話** | 「可先發布，反正後端管理員能修改」 |
| 委託人理由 | 研究方向可由管理者日後於後台自行補齊，不需為此阻擋上線。 |
| 規格衝突說明<br>（審查者整理） | 四位成員的姓名是母站確實存在的內容，必須可被查得（AC-23）；研究方向母站不存在，依 §2.3 不得捏造。兩條規格無法同時滿足，故需管理者裁示。 |
| 實作方式 | `people.legacy_pending_detail = True`。`PublishValidator` 對此旗標的人物豁免「研究焦點必填」門檻，改為輸出 warning，並在 Admin Dashboard 的「需要注意」持續提醒，直到管理者補齊為止。豁免**僅限研究焦點一項**，不擴及姓名、slug 或照片 alt。 |
| 決策編號 | **ADR-012** — 全文見 `docs/adr/ADR-012-legacy-pending-publish-exemption.md` |
| 後續責任 | 研究室應儘速提供四位成員的研究方向與英文姓名，由管理者於後台補齊。補齊後系統會自動解除待補標記。 |

### 2.3 專案建立位置

| 項目 | 內容 |
| --- | --- |
| **裁示結果** | `NTUST SiPh Lab WEB/ntust-siph-lab/` |
| 核准日期 | 2026-08-15 |
| 理由 | 依 SAI §9.4 目錄樹以 `ntust-siph-lab/` 為專案根目錄，SAI PDF 保留在外層作為規格來源，維持 git repo 邊界乾淨。 |

---

## 3. 逐項核對表（SAI 附錄 C — Legacy content checklist）

| # | 檢查項目 | 狀態 | 證據 |
| --- | --- | --- | --- |
| 1 | 重新抓取母站 snapshot，日期與來源 URL 已記錄 | ✅ | `source_snapshot.md` §1 |
| 2 | LC-001 ~ LC-019 全部存在於 migration inventory | ✅ | `migration_inventory.csv`（19 筆） |
| 3 | 教授基本資料、六項專長、Email、外鏈、圖片、四位成員皆有新站 target | ✅ | `content_mapping.csv`；AC-23 驗證通過 |
| 4 | Google Calendar embed 有明確保留/移除決策 | ✅ | 本檔 §2.1；AC-25 驗證通過 |
| 5 | Google Sites platform chrome 僅依 exclusion list 排除 | ✅ | `difference_report.md` §4（6 項） |
| 6 | difference_report 無 UNRESOLVED | ✅ | `difference_report.md` §3 |
| 7 | 所有 APPROVED_REWRITE / APPROVED_REMOVE 有核准紀錄 | ✅ | 本檔 §2.1（唯一一筆 APPROVED_REMOVE） |
| 8 | content_signoff 完成後才允許舊站改成搬遷公告 | ⏳ | **待 cutover 時執行**（見 §5） |

---

## 4. 母站內容逐項對照（AC-23 明列項目）

| Legacy ID | 母站原文 | 新站可見位置 | 比對方式 | 結果 |
| --- | --- | --- | --- | --- |
| LC-001 | NTUST SiPh Lab | Header、首頁、footer、metadata | exact | ✅ |
| LC-002 | 教授頁首圖片 | `/about`、`/members`、`/people/chun-liang-yang` | SHA-256 checksum | ✅ |
| LC-003 | 楊淳良 / Chun-Liang Yang | `/people/chun-liang-yang` | exact | ✅ |
| LC-004 | 副教授 | 教授頁「職稱」欄 | exact | ✅ |
| LC-005 | 國立臺灣科技大學電子工程博士 | 教授頁「學歷」欄 | exact | ✅ |
| LC-006 | 光電感測技術 | 首頁與 `/about` 研究方向、教授頁研究專長 | set 比對 | ✅ |
| LC-007 | 矽光子技術 | 同上 | set 比對 | ✅ |
| LC-008 | 光通道效能監視 | 同上 | set 比對 | ✅ |
| LC-009 | 光通訊系統 | 同上 | set 比對 | ✅ |
| LC-010 | 物聯網平台 | 同上 | set 比對 | ✅ |
| LC-011 | 人工智慧技術應用 | 同上 | set 比對 | ✅ |
| LC-012 | yangcl@mail.ntust.edu.tw | 教授頁、`/join`、footer | exact（小寫正規化） | ✅ |
| LC-013 | NTUST Website 外鏈 | 教授頁、`/about`、footer | HTTP 200 驗證 | ✅ |
| LC-014 | 研究室成員 section | `/members`（並擴充個人頁與 `/alumni`） | 頁面存在 | ✅ |
| LC-015 | 碩二生 陳泓序 | `/members`、`/people/hong-xu-chen` | exact | ✅ |
| LC-016 | 碩二生 謝卓雅 | `/members`、`/people/zhuo-ya-xie` | exact | ✅ |
| LC-017 | 碩二生 鍾宇辰 | `/members`、`/people/yu-chen-zhong` | exact | ✅ |
| LC-018 | 碩二生 陳志翰 | `/members`、`/people/zhi-han-chen` | exact | ✅ |
| LC-019 | Google Calendar 嵌入 | —（經核准不搬遷） | 核准紀錄 | ✅ |

---

## 5. Cutover 前的最終簽核

> 以下區塊必須在**正式網域切換前**由教授／管理者親自確認並填寫。
> 在此之前，舊 Google Sites **不得**關閉或改為搬遷公告（SAI §22.4 步驟 9）。

- [ ] 我已在新站實際瀏覽並確認教授姓名、職稱、學歷、六項專長、Email 與外部連結完全正確。
- [ ] 我已確認四位在學成員的姓名正確無誤。
- [ ] 我已知悉四位成員的研究方向尚未填寫，並同意先行上線。
- [ ] 我已確認 Google Calendar 嵌入的移除決定。
- [ ] 我已確認新站沒有任何憑空產生的研究成果或畢業生資料。
- [ ] `python scripts/verify_migration.py --all` 於 cutover 當日重新執行且通過。

| 欄位 | 內容 |
| --- | --- |
| 簽核人姓名 | ＿＿＿＿＿＿＿＿＿＿ |
| 職稱 | ＿＿＿＿＿＿＿＿＿＿ |
| 簽核日期 | ＿＿＿＿＿＿＿＿＿＿ |
| 備註 | ＿＿＿＿＿＿＿＿＿＿ |

---

## 6. 維護說明

- 本檔的 §1 數值必須與 `difference_report.md` 一致。
  若兩者不符，表示有人手動編輯了其中之一 —— 請重新執行
  `python scripts/verify_migration.py --all` 並以自動產生的結果為準。
- 任何新增的 APPROVED_REWRITE / APPROVED_REMOVE 決策，
  都必須在本檔 §2 增列一節，並同步更新
  `scripts/legacy_baseline.py` 的 `approval` 欄位。
- 本檔為稽核紀錄，**只增不刪**。裁示如有變更，請新增一節說明變更理由，
  不要覆寫原有紀錄。
