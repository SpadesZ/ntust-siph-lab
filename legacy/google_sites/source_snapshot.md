# NTUST SiPh Lab — 母站 Source Snapshot

> **檔案路徑**：`legacy/google_sites/source_snapshot.md`
> **建立日期**：2026-08-15　**版本**：v1.0
> **用途**：SAI §22.4 Migration Runbook 步驟 1「Freeze snapshot」的紀錄。
> 保存母站上線遷移前的原始內容、來源 URL、資產清單與抓取日期。

---

## 1. 抓取資訊

| 項目 | 內容 |
| --- | --- |
| 母站 URL | <https://sites.google.com/view/ntust-siph-lab/> |
| SAI 文件原始盤點日期 | 2026-08-14（見 SAI v1.2 §2、[S1]） |
| 本次重新抓取日期 | 2026-08-15 |
| 抓取方式 | HTTP GET 頁面內容擷取 + 圖片資產直接下載 |
| 兩次盤點結果 | **一致**，母站在 2026-08-14 至 2026-08-15 期間無內容變動 |
| 頁面結構 | 單頁式（single page），無子頁面 |

### 為什麼要重新抓取

SAI §22.4 步驟 1 要求「上線遷移前重新抓母站，保存 source URL、文字
snapshot、截圖/資產清單與抓取日期」。SAI 文件本身的盤點完成於 2026-08-14，
本次實作於 2026-08-15 重新驗證，確認 Legacy Baseline 未過期。

---

## 2. 母站可見內容全文（逐字保存）

以下為母站當下可見的**全部 Lab 內容**，未經改寫。
此為 difference report 進行 exact match 比對的基準（SAI §22.5）。

### 2.1 研究室識別

```
NTUST SiPh Lab
```

### 2.2 教授資訊

```
楊淳良
Chun-Liang Yang
副教授 / Associate Professor
國立臺灣科技大學電子工程博士
yangcl@mail.ntust.edu.tw
```

### 2.3 研究專長（六項，順序即母站呈現順序）

```
光電感測技術
矽光子技術
光通道效能監視
光通訊系統
物聯網平台
人工智慧技術應用
```

母站以頓號連接呈現為單一字串：

```
光電感測技術、矽光子技術、光通道效能監視、光通訊系統、物聯網平台、人工智慧技術應用
```

### 2.4 外部連結

| 顯示文字 | 目標 URL | 2026-08-15 HTTP 狀態 |
| --- | --- | --- |
| NTUST Website | `https://innc.ntust.edu.tw/p/412-1111-12071.php?Lang=zh-tw` | 200（可達，無轉址） |

### 2.5 研究室成員

```
研究室成員

碩二生 陳泓序
碩二生 謝卓雅
碩二生 鍾宇辰
碩二生 陳志翰
```

**重要**：母站對這四位成員**只提供中文姓名與「碩二生」年級**。
沒有英文姓名、研究方向、論文題目、照片或聯絡方式。
依 SAI §2.3 與 §23.1，這些欄位不得由 Agent 推測填入。

### 2.6 嵌入內容

| 項目 | 說明 |
| --- | --- |
| Google Calendar | 台灣假日日曆（agenda 檢視），以 iframe 嵌入 |

---

## 3. 資產清單

| Legacy ID | 類型 | 原始 URL | 本地保存路徑 | 大小 | SHA-256 | 取得狀態 |
| --- | --- | --- | --- | --- | --- | --- |
| LC-002 | JPEG 圖片<br>400×468 | `https://lh3.googleusercontent.com/sitesv/AG8ngQW…w1280`（完整 URL 見 `scripts/legacy_baseline.py`） | `legacy/google_sites/assets/LC-002_professor_photo.jpg` | 40,847 bytes | `1d46458f9eace22ef75c9436995216ab12b1240a4dfa2edca3622ebf68221a1f` | **已取得原檔** |

### 資產取得說明

SAI §2.1 對 LC-002 的處理指示為「MIGRATE；需取得原檔或可接受品質版本」，
且 §23.1 最後一條規定：Agent 若無法取得圖片/embed 原始資產，
必須建立 UNRESOLVED issue，而不是用空白、任意替代圖或靜默略過。

本次已成功下載**原始檔案**（HTTP 200，Content-Type: image/jpeg），
並以 SHA-256 記錄。因此 LC-002 **不構成 UNRESOLVED**，AC-24 通過。

原檔永久保存於 `legacy/google_sites/assets/`，即使日後 Google Sites
關閉或圖片 URL 失效，仍可重新取用。

---

## 4. 明確排除的 Google Sites 平台元素

依 SAI §2.2，以下屬於 Google Sites 平台自有的 UI/boilerplate，
**不是 Lab 內容**，因此不納入遷移清冊。
此處記錄是為了證明它們是「依 exclusion list 排除」而非「遺漏」（AC-25）。

| 元素 | 處理 | 理由 |
| --- | --- | --- |
| Search this site | DO NOT MIGRATE | Google Sites 平台導覽/UI |
| Skip to main content | DO NOT MIGRATE | 平台導覽；新站有自有 skip link |
| Skip to navigation | DO NOT MIGRATE | 平台導覽 |
| Page updated | DO NOT MIGRATE | 平台 footer；新站以 `updated_at` 與「最後更新」取代 |
| Google Sites | DO NOT MIGRATE | 平台品牌標示；新站有自有 footer |
| Report abuse | DO NOT MIGRATE | 平台功能連結 |

---

## 5. 母站**不存在**的內容（新站不得憑空產生）

以下為新站有版型但母站沒有資料的項目。
依 SAI §2.3：「研究成果、畢業生去向、成員英文名等母站不存在的資料，
不得由 Agent 猜測填入；需另有 Lab 確認來源。」

| 項目 | 母站狀態 | 新站處理 |
| --- | --- | --- |
| 研究成果（論文/專案/原型） | 完全不存在 | 版型與後台已完整實作，內容為空，待研究室提供 |
| 畢業生 | 完全不存在 | 版型與後台已完整實作，內容為空 |
| 四位學生的英文姓名 | 不存在 | 留空，標記待補 |
| 四位學生的研究方向 | 不存在 | 留空，標記待補（`legacy_pending_detail`） |
| 四位學生的論文題目 | 不存在 | 留空 |
| 六項專長的定義說明 | 只有名稱，無說明文字 | 名稱已遷入，說明欄留空待補 |
| 研究室地址 | 不存在 | 留空，待管理者填寫 |
| 系所名稱 | 不存在（僅可由教授學歷推知學校） | 學校已填，系所留空待管理者確認 |
| 招募說明 | 不存在 | 留空，待管理者填寫 |

**這些空白是刻意且正確的。** 填入推測內容會違反 SAI §2.3、§14.3
與 `skills/siph-lab-seo-geo/SKILL.md` 的 publish gate。

---

## 6. 下一步

1. 本檔為 cutover 前的凍結基準，**不得**在遷移過程中修改。
2. 若母站在 cutover 前有更新，必須：
   - 重新抓取並更新本檔與 `scripts/legacy_baseline.py`
   - 更新 `CAPTURED_AT`
   - 重新執行 `python scripts/verify_migration.py --all`
3. 正式 cutover 完成且 `content_signoff.md` 簽核後，
   才可將舊 Google Sites 改為搬遷公告（SAI §22.4 步驟 9）。
