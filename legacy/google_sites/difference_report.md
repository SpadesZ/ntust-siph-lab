# NTUST SiPh Lab — Legacy Migration Difference Report

> 本檔由 `python scripts/verify_migration.py` 自動產生，請勿手動編輯。
> 手動修改會與實際驗證結果脫節，違反 ADR-011 的證據鏈要求。

- 產生時間：2026-08-15T12:52:00.098241+08:00
- 母站來源：https://sites.google.com/view/ntust-siph-lab/
- 盤點時間：2026-08-15T00:00:00+08:00
- Inventory 總筆數：19

## 1. 摘要

| 項目 | 數值 |
| --- | --- |
| APPROVED_REMOVE | 1 |
| MIGRATED | 18 |
| **UNRESOLVED（含驗證失敗）** | **0** |

> **狀態：可進行 cutover。** UNRESOLVED = 0，AC-21~AC-26 全數通過。

## 2. 驗收項目檢查結果

### ✅ AC-21 — migration_inventory 欄位完整性

通過 19 項：

- LC-001：欄位完整（status=MIGRATED）
- LC-002：欄位完整（status=MIGRATED）
- LC-003：欄位完整（status=MIGRATED）
- LC-004：欄位完整（status=MIGRATED）
- LC-005：欄位完整（status=MIGRATED）
- LC-006：欄位完整（status=MIGRATED）
- LC-007：欄位完整（status=MIGRATED）
- LC-008：欄位完整（status=MIGRATED）
- LC-009：欄位完整（status=MIGRATED）
- LC-010：欄位完整（status=MIGRATED）
- LC-011：欄位完整（status=MIGRATED）
- LC-012：欄位完整（status=MIGRATED）
- LC-013：欄位完整（status=MIGRATED）
- LC-014：欄位完整（status=MIGRATED）
- LC-015：欄位完整（status=MIGRATED）
- LC-016：欄位完整（status=MIGRATED）
- LC-017：欄位完整（status=MIGRATED）
- LC-018：欄位完整（status=MIGRATED）
- LC-019：欄位完整（status=APPROVED_REMOVE）

### ✅ AC-22 — UNRESOLVED = 0（launch gate）

通過 1 項：

- 全部 19 筆皆為可上線狀態：APPROVED_REMOVE=1、MIGRATED=18

### ✅ AC-23 — 母站內容在新站可被找到

通過 14 項：

- LC-003 姓名（中）：逐字相符
- LC-003 姓名（英）：逐字相符
- LC-004 職稱：逐字相符
- LC-005 學歷：逐字相符
- LC-012 Email：逐字相符
- LC-013 NTUST 外鏈：逐字相符
- 教授頁面已發布：/people/chun-liang-yang
- LC-006~011 六項專長：SiteSetting 六項全數存在
- LC-006~011 六項專長：教授頁六項全數存在
- LC-015：「陳泓序」已發布（/people/hong-xu-chen）
- LC-016：「謝卓雅」已發布（/people/zhuo-ya-xie）
- LC-017：「鍾宇辰」已發布（/people/yu-chen-zhong）
- LC-018：「陳志翰」已發布（/people/zhi-han-chen）
- LC-001 Lab 名稱：逐字相符（NTUST SiPh Lab）

### ✅ AC-24 — 媒體資產 manifest 與 checksum

通過 3 項：

- LC-002 原始資產 checksum 相符（1d46458f9eace22e…，40847 bytes）
- LC-002：照片已存在於 storage（local），object key=people/715eb8e46a514e629f75e9e8e11eaf70.jpg
- LC-002：照片替代文字已設定（楊淳良副教授照片）

### ✅ AC-25 — 平台元素排除與 embed 決策

通過 2 項：

- SAI §2.2 的 6 項平台元素皆有明確排除理由
- LC-019 已有明確決策：APPROVED_REMOVE

### ✅ G3/G4 — 資料庫完整性（counts / slug / FK）

通過 7 項：

- 筆數：people=5、research_outputs=0、research_output_people=0
- people.slug 全部唯一
- research_outputs.slug 全部唯一
- people.slug 無空值
- research_outputs.slug 無空值
- research_output_people 無孤兒關聯
- people 時間欄位完整（created_at / updated_at）

## 3. UNRESOLVED 清單（Launch Gate）

無。所有母站內容皆已 MIGRATED 或取得明確核准。

## 4. 平台元素排除紀錄（SAI §2.2 / AC-25）

以下為 Google Sites 平台自有的 UI/boilerplate，
依 SAI §2.2 明確排除，非內容遺漏：

| 元素 | 排除理由 |
| --- | --- |
| Search this site | Google Sites 平台導覽/UI，不是 Lab 內容（SAI §2.2）。 |
| Skip to main content | Google Sites 平台導覽/UI；新站以自有 skip link 取代（SAI §2.2）。 |
| Skip to navigation | Google Sites 平台導覽/UI（SAI §2.2）。 |
| Page updated | 平台 footer/boilerplate；新站以 updated_at 與 last-updated 顯示取代（SAI §2.2）。 |
| Google Sites | 平台品牌標示；新站以自有 footer 取代（SAI §2.2）。 |
| Report abuse | 平台功能連結，非 Lab 內容（SAI §2.2）。 |
