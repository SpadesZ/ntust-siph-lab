# NTUST SiPh Lab SAI v1.2

> **這是規格書的純文字版本，供 grep、diff 與 code review 引用。**
> 權威版本是同目錄下的 `NTUST_SiPh_Lab_SAI_v1.2.pdf`（含表格排版與圖）。
> 兩者內容相同；若有出入以 PDF 為準。
>
> 本檔由 PDF 自動抽取產生（pypdf），未經人工改寫，以確保
> 不會在轉換過程中偏離原始需求。表格在純文字化後欄位會換行，
> 需要精確閱讀表格時請看 PDF。
>
> SAI §23.1 規定：每次功能修改前必須先讀本文件與兩份專案 SKILL.md
> （`skills/siph-lab-web-design/SKILL.md`、`skills/siph-lab-seo-geo/SKILL.md`）。

---

SYSTEM ANALYSIS
WITH AI
NTUST SiPh Lab 官方網站重構與內容管理系統
Local-first SQLite -> Cloud Run | Flask + SQLAlchemy | SEO + GEO | Single-Admin CMS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
文件定位：可直接交付 AI Coding Agent / 開發者執行之系統分析、介面、資料、部署與驗收規格。
母版來源：NTUST SiPh Lab 現行 Google Sites。重構目標不是單純換版，而是建立可長期維護的研究室官方研究知識網站。
項目 定義
文件版本 v1.2
日期 2026-08-14
系統型態 Server-rendered research lab website + lightweight
CMS
核心技術 Local: Docker / Flask / Jinja2 / SQLAlchemy / SQLite
Production target: Cloud Run / Cloud SQL PostgreSQL /
Cloud Storage
後台帳號 單一管理帳號
後台登入 /admin/login
資料持久化 Local: SQLite + local uploads；Production: Cloud SQL
PostgreSQL + Cloud Storage
主要內容 在學碩士生 / 研究成果 / 畢業生
優先目標 直覺、可維護、研究室辨識度、SEO、GEO、可驗收
範圍鎖定 v1.2 採兩階段架構，並強制執行母站內容零遺漏遷移：開發/驗收固定使用 SQLite，不要求先建立任何 GCP 資
源；正式上線才切換 Cloud Run + Cloud SQL PostgreSQL + Cloud Storage。仍不做多人帳號、學生登入、公開註冊、
SPA、微服務或複雜工作流，所有內容由單一管理員透過 /admin 維護。
文件控制與決策摘要
決策編號 決策 狀態 理由
ADR-001 Flask + Jinja SSR 鎖定 前台直接輸出完整 HTML，維持 SEO、GEO 與維護單純
性。
ADR-002 Local-first SQLite 鎖定 開發與內容驗收階段使用單檔 SQLite；不要求先建立雲
端資料庫，降低開發成本與環境摩擦。
ADR-003 SQLAlchemy + Alembic 為
資料存取契約
鎖定 Model、query 與 migration 不得依賴 SQLite-only 技
巧，為 PostgreSQL 遷移保留可測試路徑。
ADR-004 Cloud Run 為正式上線目標鎖定 應用維持同一 Docker image；正式環境不保存任何關鍵
資料於 container filesystem [S17]。
ADR-005 Cloud SQL PostgreSQL 為正
式 DB
鎖定 Cloud Run 正式環境改用受管理的 PostgreSQL；Cloud
Run 可直接連 Cloud SQL [S18][S19]。
ADR-006 Cloud Storage 為正式媒體
儲存
鎖定 人物照片與研究成果圖片改為 object storage，不依賴
Cloud Run 暫存檔案系統 [S20]。
ADR-007 單一 Admin 帳號 鎖定 符合實際維護需求，避免過早導入 RBAC。
ADR-008 Person 單一人物實體 鎖定 在學生畢業只改狀態，不搬資料；成果作者關聯不斷
裂。
ADR-009 ResearchOutput 統一成果
實體
鎖定 期刊、會議、專案、模擬、系統等以 type 分流，不複製
資料模型。
ADR-010 SEO 為基礎、GEO 為結構化
增強
鎖定 以核心 SEO、可見內容與結構化資料為主，不採不可驗
證的 AI 排名捷徑。
ADR-011 Legacy Content
Preservation Gate
鎖定 母站所有經確認有效的 Lab 公開內容必須建立
inventory 與 new-site mapping；未獲教授/管理者明確
批准不得刪除、遺漏或以推測內容取代。
1. 執行摘要
本案將現行 NTUST SiPh Lab Google Sites 由「教授資訊 + 成員姓名清單」提升為一套可被人閱讀、被搜尋引擎理解、被 AI
搜尋引用，並能由非工程人員自行維護的研究室官方網站。教授指定的三項核心內容 - 在學碩士生、研究成果、畢業生 - 各自
有清楚版型，後端則透過 Person、ResearchOutput 與關聯資料建立可長期累積的研究知識結構。
v1.2 新增硬性原則 - Legacy Content Preservation：現行母站 https://sites.google.com/view/ntust-siph-lab/ 中所
有經確認屬於 Lab 的公開文字、人物資料、圖片、外部連結與嵌入內容，必須先進入遷移清冊，再在新站找到對應位
置。除非教授/管理者明確核准「刪除、合併、改寫」，Agent 不得自行省略。[S1]
v1.2 延續 Local-first 架構，並新增 Legacy Content Preservation Gate；其核心架構是把「開發環境」與「正式雲端環境」
明確分開。開發、設計 review、內容輸入與功能驗收全部先在本機完成：Docker + Flask + Jinja2 + SQLAlchemy + SQLite +
local uploads。此階段不依賴 GCP，也不需要先支付 Cloud SQL 成本。
當功能與內容確認可用後，再執行 Production Migration Gate：同一套 Flask/SQLAlchemy 程式部署到 Cloud Run，資料庫
切換為 Cloud SQL PostgreSQL，圖片與附件切換為 Cloud Storage。Cloud Run 的 container filesystem 不作永久資料保
存；Google 官方明確說明 instance 停止後檔案不會持久存在 [S17]。因此正式環境禁止把 SQLite DB 或正式 uploads 放在
container filesystem。
管理端仍採單一帳號，登入位置固定為 /admin/login。管理者可新增、編輯、排序、上/下架人物與成果，並修改首頁、Lab
基本資訊與 SEO 欄位。Cloud migration 不改變後台操作方式，教授或管理者不需要知道 SQLite、PostgreSQL 或 Cloud
Storage 的差異。
1.1 成功定義
本機在沒有任何 GCP 資源的情況下，完整跑通首頁、三類內容、Admin CRUD、SEO/GEO 與測試。
所有 ORM 與 migrations 可同時在 SQLite 測試環境與 PostgreSQL staging database 通過。
SQLite export -> PostgreSQL import 可重複執行並有筆數、關聯、slug、時間欄位與 checksum 驗證。
人物/研究成果媒體可由 local uploads 同步到 Cloud Storage，資料表只保存穩定 object key/URL metadata。
Cloud Run 新 revision 部署或 instance 重建後，資料與圖片不得消失。
學生由在學轉為畢業生時，只改人物狀態並補畢業欄位，既有研究成果關聯不斷裂。
網站具有固定 URL、sitemap.xml、robots.txt、canonical、Open Graph、semantic HTML 與適當 JSON-LD。
重要管理操作可回溯，並具有本機備份與雲端資料庫/媒體備份策略。
母站 legacy inventory 每一筆都具有 MIGRATED、APPROVED_REWRITE 或 APPROVED_REMOVE 最終狀態；正式上線前不得存在 
UNRESOLVED。
母站目前已確認的教授資訊、六項研究專長、Email、NTUST 外鏈、教授圖片與四位碩二生成員姓名，在新站都有可驗證
的對應內容或明確批准的處理紀錄。
1.2 非目標
不做學生/老師個別帳號、角色權限矩陣或公開註冊。
不做 React/Next.js SPA、微服務、Kubernetes 或自行管理 PostgreSQL VM。
開發階段不要求 Cloud SQL、Cloud Storage 或正式網域；先把產品功能做對。
正式 Cloud Run 不使用 SQLite 當永久資料庫，也不把 uploads 當 container 永久檔案。
不保證搜尋排名或 AI 引用；本案提供可被正確理解、抓取與引用的技術與內容條件。
• 
• 
• 
• 
• 
• 
• 
• 
• 
• 
• 
• 
• 
• 
• 
2. 現行母版網站盤點與 Legacy Baseline
本文件於 2026-08-14 重新讀取現行母站 https://sites.google.com/view/ntust-siph-lab/。以下項目視為 v1.2 的「Legacy
Baseline」，屬於正式 migration inventory 的最低集合 [S1]。新站不是重新憑空撰寫，而是先完整承接這些已公開資料，再
新增教授要求的研究成果、畢業生與更完整研究成員版型。
資料保存邊界：「全部搬遷」指所有經確認屬於 NTUST SiPh Lab 的內容資料與資產；Google Sites 平台本身的 UI/
boilerplate（例如 Search this site、Skip to navigation、Page updated、Google Sites、Report abuse）不是 Lab
內容，不納入搬遷。任何介於兩者之間的項目，預設為 REVIEW_REQUIRED，不可直接刪除。
2.1 Legacy Baseline Inventory
ID 母站現有內容 類型 新站預設去處 預設處理
LC-001 NTUST SiPh Lab Lab 名稱 SiteSetting / Header /
Homepage / metadata
MIGRATE
LC-002 教授頁首圖片（Google Sites 圖
片資產）
Image asset Professor profile / Hero or
About
MIGRATE；需取得原檔
或可接受品質版本
LC-003 Chun-Liang Yang / 楊淳良 Person nameProfessor profile / Person
entity
MIGRATE EXACT
LC-004 副教授 / Associate ProfessorTitle Professor profile MIGRATE EXACT，除非
管理者更新
LC-005 國立臺灣科技大學電子工程博士Education Professor profile / AboutMIGRATE EXACT
LC-006 光電感測技術 Expertise Research Focus /
Professor profile
MIGRATE
LC-007 矽光子技術 Expertise Research Focus /
Professor profile
MIGRATE
LC-008 光通道效能監視 Expertise Research Focus /
Professor profile
MIGRATE
LC-009 光通訊系統 Expertise Research Focus /
Professor profile
MIGRATE
LC-010 物聯網平台 Expertise Research Focus /
Professor profile
MIGRATE
LC-011 人工智慧技術應用 Expertise Research Focus /
Professor profile
MIGRATE
LC-012 yangcl@mail.ntust.edu.twEmail Professor profile / ContactMIGRATE EXACT
ID 母站現有內容 類型 新站預設去處 預設處理
LC-013 NTUST Website 外部連結 External linkProfessor profile /
External links
MIGRATE；launch 前
驗證 URL
LC-014 研究室成員 Section
concept
/members MIGRATE + EXPAND
LC-015 碩二生 陳泓序 Current
member
Person / Current Member
template
MIGRATE EXACT；其餘
欄位待確認
LC-016 碩二生 謝卓雅 Current
member
Person / Current Member
template
MIGRATE EXACT；其餘
欄位待確認
LC-017 碩二生 鍾宇辰 Current
member
Person / Current Member
template
MIGRATE EXACT；其餘
欄位待確認
LC-018 碩二生 陳志翰 Current
member
Person / Current Member
template
MIGRATE EXACT；其餘
欄位待確認
LC-019 Google Calendar 嵌入：台灣假
日日曆（agenda）
Embedded
content
待管理者確認是否仍屬 Lab
有效需求
REVIEW_REQUIRED；
未批准前不可自行刪除
2.2 明確不屬於 Lab 內容的 Google Sites 平台元素
項目 處理 理由
Search this site / Skip to main
content / Skip to navigation
DO NOT MIGRATE Google Sites 平台導覽/UI，不是 Lab
內容。
Page updated / Google Sites /
Report abuse
DO NOT MIGRATE 平台 footer/boilerplate；新站以自己
的 footer、accessibility 與管理紀錄
取代。
2.3 Source-of-Truth 規則
Legacy Baseline 的文字以母站當下可見內容為第一來源；若教授/管理者提供更新資料，必須在 mapping 中記錄 
APPROVED_REWRITE 與核准人/日期。
母站外部連結所指向的頁面可作「補充查證」，但外部頁面的新增資訊不會自動變成母站必搬內容。
圖片、embed、PDF 等非純文字資產必須列入 manifest；無法直接下載時，要從 Google Sites 管理端取得原檔或由管理
者確認替代檔。
研究成果、畢業生去向、成員英文名等母站不存在的資料，不得由 Agent 猜測填入；需另有 Lab 確認來源。
• 
• 
• 
• 
3. 使用者、角色與核心情境
Persona 主要目的 典型問題 成功結果
潛在研究生 判斷研究室是否符合興趣Lab 做什麼？目前學生在做
什麼？我加入後可能做什
麼？
3 分鐘內理解研究方向、成
員與代表成果。
學術訪客/合作方 確認能力與過往產出 是否做過 SiPh、光通訊、
AI 相關題目？有哪些論文或
系統？
可從成果頁取得清楚摘要、
年份、作者、連結。
在學學生 對外建立研究身份 我的研究方向與成果是否能
被找到？
有固定個人頁並與成果雙向
關聯。
畢業生 保留研究傳承 當年論文題目與研究方向是
否仍可查？
按畢業年度可找到人物與論
文。
單一管理員 維護全部公開資訊 如何不碰程式就更新？ 登入 /admin 後在 3-5 分鐘
內完成一般新增/編輯。
搜尋/AI 系統 理解實體與關係 誰、做什麼、何時、在哪
裡、與哪些成果相關？
HTML 與 structured data
對齊、URL 穩定、內容可抓
取。
3.1 管理角色鎖定
只有一個管理帳號 v1.2 僅存在 Admin。沒有 Editor、Student、Teacher、Reviewer。Admin 可管理全部公開內容與
網站設定。帳號資料仍以 admin_users table 儲存，避免把帳密硬編碼在 .env 或 Python；未來若真的需要多人，才擴
充 RBAC。
4. 資訊架構 IA
圖 1. 建議前台資訊架構：教授指定的三類內容各自獨立，但透過 Person / ResearchOutput 關聯形成研究知識網。
4.1 主導覽
導覽 Route 頁面工作 是否進主選單
Home / 建立 Lab 定位與進入點是
About /about 教授、Lab、研究方向與設
備/方法概覽
是
Members /members 在學碩士生總覽 是
Research /research 研究成果總覽與篩選 是
Alumni /alumni 畢業生年度/研究主題總覽是
Join /join 招募、聯絡、位置 是
Person detail /people/<slug> 人物固定 URL；依 current/
alumni 呈現不同區塊
由列表進入
Research detail /research/<slug> 單一成果完整資訊 由列表/個人頁進入
Admin login /admin/login 單一管理員登入 否
Admin /admin CMS dashboard 否
4.2 URL 原則
URL 使用英文小寫 slug，避免姓名/標題直接做 URL encoding：/people/hong-xu-chen。
slug 一旦公開即視為永久識別；更名時建立 Redirect 記錄，不直接讓舊 URL 404。
同一內容只有一個 canonical URL；列表排序、追蹤參數不可產生新的 canonical。
若啟用完整雙語，建議 /zh/ 與 /en/ 形成對稱路由；若翻譯不完整，不建立假的 hreflang 對應頁。
v1 可先以繁體中文為主、英文欄位為可選，但 DB 欄位預留 *_zh / *_en，避免日後大改 schema。
• 
• 
• 
• 
• 
5. 前台頁面規格
5.1 首頁 Home
順序 Section 內容 主要 CTA / SEO 目的
01 Hero thesis Lab 中英文名稱 + 一句明確
研究定位 + SiPh 專屬視覺
Research / Join；H1 僅一
個。
02 Research focus 3-6 個核心研究主題；每項
1 句定義 + 對應成果
建立 topical context。
03 Featured outputs 3-6 筆管理者標記 featured
的成果
將首頁權重導向代表成果。
04 Current members 教授 + 在學學生精簡卡 建立人物實體與內部連結。
05 Lab proof 論文/成果/方法/合作等可驗
證事實，不用虛構 vanity
metrics
增加可信度與可引用事實。
06 Alumni preview 近期畢業生與年度入口 研究傳承。
07 Join / Contact 招募說明、Email、校方/實
驗室位置
明確下一步。
Hero 規則 採用 Anthropic frontend-design 的核心精神：Hero 必須是這個研究室的「論點」，內容與視覺都要從
silicon photonics / optical communication 的真實語彙出發，而不是套用大數字 + 漸層球 + 三張卡片的通用模板
[S2]。
5.2 在學碩士生版型 Current Member
列表頁的工作是「掃描與比較」，詳細頁的工作是「建立研究身份」。成員卡不塞完整履歷；每張卡只呈現能幫訪客判斷研
究方向的資訊。
層級 必顯示 選配 禁止
列表卡 照片、姓名、中英文名、年
級/狀態、1-2 行研究焦點、
2-4 個技能/主題 tag
GitHub / Scholar icon 私人電話、未同意公開的個
資、過長自傳。
詳細頁首屏 姓名、身份、研究焦點一句
話、照片、公開聯絡/學術
連結
ORCID、LinkedIn 把所有 metadata 堆在首
屏。
層級 必顯示 選配 禁止
詳細內容 Thesis / Current Topic、
Research Interests、
Methods & Tools、
Related Outputs
精選圖、公開程式碼 空泛「熱愛研究」式內容。
頁尾 Related Research、Back
to Members、Last
updated
Join CTA 無下一步的死路。
5.3 研究成果版型 Research Output
研究成果詳細頁是 SEO/GEO 最重要的內容單位。每筆成果都必須回答「問題、方法、結果、意義」，並提供可驗證的作者、
年份、venue/DOI/外部連結。不是所有成果都是論文：若為期刊/會議論文可輸出 ScholarlyArticle；若為一般研究展示則使
用 CreativeWork 等較保守類型。
區塊 目的 欄位/呈現
Header 快速識別成果 type badge、年份、title zh/en、作
者、venue、DOI/External link。
Executive summary 讓人快速理解，不是為 AI 硬切 chunk約 80-180 字，說清楚 Problem /
Method / Result / Significance。
Research question 定義問題 1-3 個可直接回答的研究問題。
Method 可重現層級的高階方法 模型、模擬工具、系統架構、材料/資
料來源。
Key results 呈現證據 數值、表現、觀察、圖表說明；不可
只寫「效果良好」。
Significance / limitation 避免過度宣稱 貢獻、適用範圍、限制。
Related people 建立實體關係 Lab 成員/畢業生連回 person
pages。
References / Links 可驗證性 DOI、出版社、GitHub、dataset、
demo。
Metadata SEO/GEO updated date、canonical、JSON-
LD、Open Graph。
5.4 畢業生版型 Alumni
區塊 必要欄位 隱私規則
列表 姓名、畢業年度、論文題目或研究主
題
只顯示已設定公開的人。
區塊 必要欄位 隱私規則
詳細頁 姓名、畢業年度、學位、thesis
title、research interests、related
outputs
就業公司/職稱只有本人或實驗室已確
認可公開才填。
連結 LinkedIn / ORCID / Scholar / GitHub
（若公開）
不以爬取方式自動收集私領域資訊。
結構資料 Person + alumniOf / affiliation（有
事實依據時）
structured data 必須與頁面可見內容
一致 [S7]。
6. 視覺設計系統與 SiPh Lab 自有 Style
本案不直接複製 GitHub Skill 的美學結果，而是吸收其決策方式，建立專屬 Design System。Anthropic frontend-design 強
調先鎖定 subject、audience、page job，並從主題本身的材料、儀器、符號與語彙中取得視覺識別 [S2]；Microsoft
frontend-design-review 亦要求有清楚 aesthetic direction、design token、responsive 與 accessibility 驗收 [S4]。
6.1 建議美學方向：Photon Trace Editorial
元素 規格 理由
核心意象 微環 ring、waveguide 路徑、光訊號
trace、晶片/量測圖的細線幾何
直接來自 SiPh 主題，不需要通用「AI
腦」圖。
色彩 Deep Navy 為主，Photon Teal/Cyan
做少量焦點，白/霧灰承載內容
研究感、可讀、非俗套全頁霓虹。
排版 Editorial grid + 大留白 + 明確的資料
標籤；必要時非對稱但保持掃讀性
建立研究刊物而非 SaaS landing
page 感。
字體 繁中以高品質 CJK 字體；Latin 可用
具工程感但可讀的 pairing；實作前確
認授權/載入成本
避免只為「特別」而犧牲繁中閱讀。
動態 只在 Hero / hover / filter 有目的地使
用；prefers-reduced-motion 必須可
關閉
避免 AI 模板常見的無目的漂浮動畫。
圖片 人物照、實驗/模擬圖、ring/
waveguide 圖優先；抽象生成圖只能
做弱背景
增加研究真實性與 E-E-A-T。
6.2 Design Tokens
/* static/css/tokens.css */
:root {
--color-ink: #17212B;
--color-navy: #10233D;
--color-photon: #00A6A6;
--color-photon-bright: #2ED6D6;
--color-surface: #FFFFFF;
--color-surface-muted: #F2F6F8;
--color-line: #D6E0E5;
--radius-sm: 8px;
--radius-md: 14px;
--space-1: 4px;
--space-2: 8px;
--space-3: 12px;
--space-4: 16px;
--space-6: 24px;
--space-8: 32px;
--space-12: 48px;
--content-max: 1180px;
}
6.3 UI 禁止清單
禁止因為「科技」就整站套紫藍漸層、玻璃擬態與發光圓球。
禁止首頁每個 section 都是三張等寬卡片；結構要根據內容語義選擇。
禁止用虛構的 publication count、citation count、success rate 填版。
禁止把裝飾性 01/02/03 用在沒有順序意義的內容上；結構標記必須有資訊意義 [S2]。
禁止 hover 才出現唯一操作；鍵盤與觸控裝置仍要可用。
禁止 body font 小於 16px 作為桌機預設；研究摘要長文需確保行長與 line-height。
• 
• 
• 
• 
• 
• 
7. 後台 CMS - 單一管理員
7.1 登入位置與安全邊界
Public site: https://<production-domain>/
Admin login: https://<production-domain>/admin/login
Dashboard: https://<production-domain>/admin
Not authenticated:
/admin/* -> 302 redirect -> /admin/login
Authenticated:
/admin/login -> 302 redirect -> /admin
前台不需要放「Admin Login」連結，但不能把這當作安全機制。安全性來自 password hash、HTTPS、安全 cookie、
CSRF、登入頻率限制、session timeout 與所有 admin routes 的 login_required。Flask 官方建議使用 Secure / HttpOnly
等 cookie 設定並檢視安全 headers [S8]。
7.2 Dashboard
區塊 內容 操作
Quick actions 新增研究成果 / 新增在學成員 / 新增畢
業生
一鍵進表單。
Content status Current Members、Alumni、
Research Outputs 的 Published /
Draft 數量
點擊進篩選列表。
Needs attention 缺照片 alt、缺英文標題、缺 meta
description、slug 衝突等
內容品質提醒，不阻擋草稿。
Recent changes 最近 10 筆修改 可回到該內容；AuditLog 留存操作。
System health DB writable、upload writable、
backup last success
只有管理員可見。
7.3 管理側選單
/admin
├── Dashboard
├── 研究成員 Current Members
│ ├── 列表 / 搜尋 / 排序
│ ├── 新增
│ └── 編輯 / 發布 / 轉為畢業生
├── 研究成果 Research Outputs
│ ├── 列表 / type / year / status 篩選
│ ├── 新增
│ └── 編輯 / featured / 發布 / 封存
├── 畢業生 Alumni
│ ├── 年度列表
│ └── 編輯 / 發布
├── 網站設定 Site Settings
│ ├── Lab 基本資料
│ ├── 首頁 Hero / Join / Contact
│ ├── SEO defaults
│ └── Social preview defaults
└── 系統 System
├── 修改管理員密碼
├── 備份狀態
└── 登出
7.4 內容狀態
Status 前台可見 可被 sitemap 收錄 用途
draft 否 否 編輯中。
published 是 是 正式公開。
archived 預設否 否或保留舊 URL 410/
redirect 依情境
停止展示但保留資料。
7.5 在學轉畢業流程
Admin 在 Current Member 編輯頁點擊「轉為畢業生」。
系統彈出確認視窗，要求填 Graduation Year、Degree、Thesis Title（若已有可沿用）。
Person.status 從 current 變更為 alumni；同一 person id 與 slug 保持不變。
既有 ResearchOutput 關聯完全保留；個人 URL 不需更換。
前台 /members 不再顯示，/alumni 自動依年度顯示；若原 URL 是 /people/<slug>，無 redirect 問題。
AuditLog 記錄 transition 事件。
1. 
2. 
3. 
4. 
5. 
6. 
7.6 刪除政策
預設不硬刪 一般 UI 不提供一鍵永久刪除。人物/成果先 archive。若確定需要 hard delete，必須二次確認並檢查
related outputs；有關聯時禁止直接刪除，避免孤兒資料。
8. 資料模型
資料模型採「小而穩」原則。對研究室官網而言，最重要的是人物與成果的長期關聯，而不是建立通用 CMS。核心 table 共 7
類：AdminUser、Person、ResearchOutput、ResearchOutputPerson、SiteSetting、Redirect、AuditLog。
8.1 ER 關係（文字版）
AdminUser (1 row in v1)
└── writes -> AuditLog
Person 1 ─────< ResearchOutputPerson >───── 1 ResearchOutput
│ │
│ status=current/alumni/faculty │ type=journal/conference/project/
│ │ prototype/simulation/etc.
└── stable person URL └── stable research URL
SiteSetting (singleton)
Redirect (old_path -> new_path)
8.2 admin_users
欄位 型別 Required 約束/驗證 用途
id INTEGER PK Y autoincrement 識別。
username VARCHAR(80) Y unique, normalized登入帳號。
password_hash VARCHAR(255) Y 只存 hash；不可回
復明碼
驗證密碼。
is_active BOOLEAN Y default true 停用帳號。
last_login_at DATETIME N UTC 管理追蹤。
password_changed
_at
DATETIME Y UTC 安全管理。
created_at DATETIME Y UTC 稽核。
updated_at DATETIME Y UTC 稽核。
帳號數量約束 應用層在 v1 禁止新增第二個 active admin；但 schema 不以奇怪的 DB constraint 把未來擴充做死。初
始帳號使用 CLI 建立，例如 flask admin create。
8.3 people
欄位 型別 Req 規格
id INTEGER PK Y 唯一人物 ID
slug VARCHAR(120) Y unique；發布後盡量固定
status VARCHAR(20) Y faculty / current / alumni
name_zh VARCHAR(120) Y 中文姓名
name_en VARCHAR(160) N 英文姓名
degree VARCHAR(80) N M.S. / Master Student 等
entry_year INTEGER N 入學年度
graduation_year INTEGER N alumni 建議必填
research_focus_zh TEXT Y* 公開人物至少一個語言有值
research_focus_en TEXT N 英文研究焦點
thesis_title_zh TEXT N 論文題目
thesis_title_en TEXT N 英文論文題目
skills_json JSON/TEXT N 後台 tag UI，儲存陣列
bio_zh TEXT N 簡介
bio_en TEXT N 英文簡介
current_affiliation VARCHAR(200) N 畢業後單位；需確認可公開
current_position VARCHAR(160) N 畢業後職稱；需確認可公開
email_public VARCHAR(200) N 只存允許公開的 email
orcid_url VARCHAR(255) N URL validation
scholar_url VARCHAR(255) N URL validation
github_url VARCHAR(255) N URL validation
linkedin_url VARCHAR(255) N URL validation
photo_path VARCHAR(255) N uploads/people/...
photo_alt_zh VARCHAR(200) N 有 photo 則必填
photo_alt_en VARCHAR(200) N 英文頁有 photo 則建議
欄位 型別 Req 規格
sort_order INTEGER Y default 100
publish_status VARCHAR(20) Y draft/published/archived
seo_title_zh VARCHAR(180) N 空值時自動生成
seo_description_zh VARCHAR(320) N 空值時自動生成
created_at DATETIME Y UTC
updated_at DATETIME Y UTC
8.4 research_outputs
欄位 型別 Req 規格
id INTEGER PK Y 唯一成果 ID
slug VARCHAR(160) Y unique, stable
output_type VARCHAR(30) Y journal/conference/
project/prototype/
simulation/dataset/other
year INTEGER Y 4-digit
publication_date DATE N 有正式日期時使用
title_zh TEXT Y* 至少一語言必填
title_en TEXT Y* 至少一語言必填
summary_zh TEXT Y* 公開成果至少一語言有
summary
summary_en TEXT N 英文摘要
problem_zh TEXT N Problem / research
question
method_zh TEXT N Method
results_zh TEXT N Key results
significance_zh TEXT N Contribution / limitation
venue VARCHAR(255) N 期刊/會議/平台
doi VARCHAR(255) N normalized DOI; unique
where possible
欄位 型別 Req 規格
external_url VARCHAR(500) N publisher/project page
github_url VARCHAR(500) N source/repo
keywords_json JSON/TEXT N 中英研究關鍵字
hero_image_path VARCHAR(255) N 成果主圖
hero_image_alt_zh VARCHAR(220) N 有圖則必填
is_featured BOOLEAN Y default false
sort_order INTEGER Y default 100
publish_status VARCHAR(20) Y draft/published/archived
seo_title_zh VARCHAR(180) N optional override
seo_description_zh VARCHAR(320) N optional override
created_at DATETIME Y UTC
updated_at DATETIME Y UTC
8.5 research_output_people
欄位 型別 Required 用途
research_output_id FK -> research_outputs.idY 成果。
person_id FK -> people.id Y Lab 人物。
contributor_role VARCHAR(40) N author / student /
supervisor / contributor。
sort_order INTEGER Y 作者/顯示順序。
外部共同作者不一定建立 Person。ResearchOutput 另可有 authors_display_text 保存正式作者列；
research_output_people 只負責建立 Lab 內部人物與成果的可導航關聯。
8.6 site_settings
群組 欄位例 管理 UI
Identity lab_name_zh/en, short_name,
department, university
基本資訊表單。
群組 欄位例 管理 UI
Hero hero_title_zh/en, hero_intro_zh/en,
hero_media_path
首頁設定。
Contact email, address_zh/en, map_url聯絡設定。
Join join_title, join_body, join_cta招募內容。
SEO defaults default_title_suffix,
default_description,
og_image_path
SEO 設定。
External identity official_ntust_url, lab_social_linkssameAs / footer。
實作可採 singleton row（id=1）而非 key-value table，因欄位數有限且需要型別與表單驗證；schema 變更透過 Flask-
Migrate/Alembic migration 管理。
8.7 redirects
欄位 說明
old_path unique，例如 /research/old-slug
new_path 新 canonical path
status_code 永久變更預設 301
reason slug_changed / migration / manual
created_at 建立時間
8.8 audit_logs
欄位 說明
id PK
admin_user_id 操作者
action create/update/publish/archive/login/
password_change/graduate
entity_type person/research_output/site_setting/system
entity_id 可為 null
summary 短文字，不存密碼或敏感值
ip_hash_or_ip 依隱私政策決定是否保存完整 IP；v1 可只留必要資訊
欄位 說明
created_at UTC
8.9 索引與約束
people.slug UNIQUE；research_outputs.slug UNIQUE；redirects.old_path UNIQUE。
people(status, publish_status, sort_order) composite index，支援 members/alumni 列表。
research_outputs(publish_status, year, output_type) index；is_featured 可另加 index。
research_output_people(research_output_id, person_id) UNIQUE，避免重複關聯。
所有 datetime 以 UTC 儲存；前台需要時以 Asia/Taipei 顯示。
不在 DB 儲存 raw password、session secret、Google API credential。
• 
• 
• 
• 
• 
• 
9. Flask 系統架構
圖 2. v1.2 Local-first 到 Cloud Run 的目標架構：應用層保持不變，資料庫與媒體儲存依環境切換。
9.1 Layering
Layer 責任 不得做的事
Blueprint/Route 解析 request、權限檢查、呼叫
service、render template
不要把複雜 DB 邏輯塞在 route。
Form/Validator 欄位驗證、CSRF、URL/slug/file
constraint
不要直接 commit DB。
Service 內容發布、graduate transition、
redirect、SEO metadata、backup
status
不要 render HTML。
Model 資料欄位、relationship、
constraint、query helper
不要依賴 request context。
Template semantic HTML、presentation不要在 Jinja 做複雜 query。
Static CSS tokens/components、少量 JS、
images
不要放 secret 或 DB。
9.2 Request flow - 公開頁
GET /research/ring-modulator-example
-> public blueprint
-> ResearchOutput query: slug + publish_status=published
-> ContentService builds view model
-> SEOService builds title/canonical/OG/JSON-LD
-> render_template("public/research/detail.html")
-> 200 full HTML
If slug not found:
-> RedirectService checks redirects.old_path
-> 301 new_path OR 404
9.3 Request flow - 管理更新
POST /admin/research/<id>/edit
-> @login_required
-> CSRF validation
-> form validation
-> ResearchService.update(...)
-> sanitize / normalize
-> update DB transaction
-> if slug changed: create Redirect(old -> new)
-> write AuditLog
-> PRG: redirect to GET detail/list
-> flash success
9.4 目錄樹 - 正式版
ntust-siph-lab/
├── README.md
├── .env.example
├── .gitignore
├── Dockerfile
├── docker-compose.yml              # local development / QA
├── requirements.txt
├── requirements-dev.txt
├── gunicorn.conf.py
├── wsgi.py
│
├── app/
│   ├── __init__.py                 # create_app()
│   ├── config.py                   # Local/Test/Cloud config
│   ├── extensions.py               # db, migrate, login, csrf, limiter
│   ├── models/
│   │   ├── admin_user.py
│   │   ├── person.py
│   │   ├── research_output.py
│   │   ├── site_setting.py
│   │   ├── redirect.py
│   │   └── audit_log.py
│   ├── blueprints/
│   │   ├── public/
│   │   ├── auth/                   # /admin/login, logout
│   │   └── admin/
│   ├── repositories/               # DB-portable query boundary
│   │   ├── people.py
│   │   ├── research.py
│   │   └── settings.py
│   ├── services/
│   │   ├── person_service.py
│   │   ├── research_service.py
│   │   ├── seo_service.py
│   │   ├── schema_service.py
│   │   └── media_service.py        # local | gcs backend
│   ├── storage/
│   │   ├── base.py                 # StorageBackend interface
│   │   ├── local.py                # development uploads
│   │   └── gcs.py                  # Cloud Storage
│   ├── templates/
│   ├── static/
│   └── utils/
│
├── instance/                       # LOCAL ONLY
│   └── siph_lab.db
├── uploads/                        # LOCAL ONLY
│   ├── people/
│   ├── research/
│   └── site/
├── backups/                        # LOCAL backup snapshots
│
├── migrations/                     # Alembic; SQLite + PostgreSQL compatible
│   └── versions/
│
├── scripts/
│   ├── seed_from_google_sites.py
│   ├── create_admin.py
│   ├── backup_sqlite.py
│   ├── export_sqlite.py            # normalized migration package
│   ├── import_postgres.py
│   ├── sync_media_to_gcs.py
│   ├── verify_migration.py
│   └── smoke_cloud.py
│
├── deploy/
│   ├── cloudrun.md
│   ├── service.yaml                # optional declarative Cloud Run config
│   └── migration-runbook.md
│
├── legacy/                          # 母站零遺漏遷移證據
│   └── google_sites/
│       ├── source_snapshot.md       # 原文/來源 URL/抓取日期
│       ├── migration_inventory.csv  # 每一筆 legacy item
│       ├── content_mapping.csv      # old -> new location/status
│       ├── media_manifest.csv       # image/embed/external asset
│       ├── difference_report.md     # unresolved / approved deltas
│       └── content_signoff.md       # 教授/管理者核准紀錄
│
├── tests/
│   ├── test_auth.py
│   ├── test_people.py
│   ├── test_research.py
│   ├── test_seo.py
│   ├── test_schema.py
│   ├── test_storage_backends.py
│   ├── test_db_portability.py
│   └── test_migration_roundtrip.py
│
├── skills/
│   ├── siph-lab-web-design/SKILL.md
│   └── siph-lab-seo-geo/SKILL.md
│
└── docs/
    ├── SAI.md
    ├── database.md
    ├── content-guide.md
    ├── local-development.md
    ├── cloudrun-deployment.md
    └── acceptance-checklist.md
9.5 為什麼 v1 不需要 public API
公開頁直接由 Flask + Jinja 輸出 HTML，可在首個 response 中提供完整內容、metadata 與 JSON-LD，不需要先載 JS 再
fetch API。CMS 也是 server-rendered form。除 health check 外，不建立 /api/v1 可降低權限、CORS、版本治理與攻擊
面。若未來要提供 external dataset 或前後端分離，再新增 API，不要為了「看起來現代」預先複雜化。
10. Local-first 資料持久化與可遷移性
v1.2 將 SQLite 定義為「開發與驗收資料庫」，不是正式 Cloud Run 的永久資料庫。目的不是現在就把架構做複雜，而是讓
前 90% 的開發工作可在單機完成，同時避免最後一天才發現 ORM、migration 或 media path 被 SQLite/本機檔案系統綁
死。
10.1 Local docker-compose.yml
services:
  web:
    build: .
    env_file: .env.local
    command: gunicorn -c gunicorn.conf.py wsgi:app
    ports:
      - "127.0.0.1:8000:8000"
    volumes:
      - ./instance:/app/instance
      - ./uploads:/app/uploads
      - ./backups:/app/backups
# .env.local
APP_ENV=local
DATABASE_URL=sqlite:////app/instance/siph_lab.db
STORAGE_BACKEND=local
UPLOAD_DIR=/app/uploads
10.2 Database Portability Contract
所有一般 CRUD 透過 SQLAlchemy ORM / repository 執行，不在 route 中直接寫 raw SQL。
禁止依賴 SQLite-only function、PRAGMA 作為業務邏輯；若測試/維運需要 PRAGMA，必須限制在 local adapter。
Primary key、foreign key、unique、nullable、length、timestamp、enum/check constraint 必須在 migration 中明
確定義。
所有 datetime 儲存 UTC；不要依賴 SQLite 動態型別寬鬆行為。
Alembic migration 必須在 SQLite test 與 PostgreSQL migration test 都能從 empty database 升到 head。
10.3 Local SQLite 執行政策
項目 規格
用途 本機開發、UI/content review、單一 Admin 功能驗收。
連線 SQLAlchemy；短 transaction；foreign key
enforcement；busy timeout。
WAL 可評估 WAL 以改善本機讀寫並行；備份時必須使用
SQLite-aware snapshot [S10][S11]。
禁止 不得把 SQLite DB 直接帶到 Cloud Run container 當正式
永久資料庫。
• 
• 
• 
• 
• 
10.4 Media Storage Adapter
程式只依賴 StorageBackend 介面，例如 save()、delete()、public_url()。Local backend 寫入 uploads/；Cloud
backend 使用 Cloud Storage。Cloud Storage 官方提供 Python client 上傳 object 的標準 API [S20]。DB 儲存的是 object
key、mime、size、alt、checksum，不儲存 container 絕對路徑。
10.5 Migration Package
export_sqlite.py 產生版本化 migration package：manifest.json、各 table JSON/CSV、media manifest、schema
revision、row counts、checksums。import_postgres.py 只接受明確 schema revision；import 後執行 foreign-key、
unique slug、row-count、relationship 與抽樣內容驗證。
10.6 本機備份與 Restore Drill
使用 SQLite Online Backup API 或等價的一致 snapshot 方法建立 DB 備份 [S11]。
將 uploads 與 DB snapshot 一起產生 manifest/checksum。
定期在全新 temporary directory 還原，執行 migration revision、integrity、首頁與 Admin CRUD smoke test。
正式上雲後，本機備份仍保留作「開發內容快照」，但正式資料的備份責任轉由 Cloud SQL / Cloud Storage 策略承擔。
1. 
2. 
3. 
4. 
11. Authentication 與 Security
11.1 密碼與 session
控制 規格
Password hash 使用 Werkzeug generate_password_hash /
check_password_hash 的當前安全預設；DB 永不存
plaintext。
SECRET_KEY 只從環境注入；production 必須高熵且不可 commit。
SESSION_COOKIE_SECURE production=true，僅 HTTPS。
SESSION_COOKIE_HTTPONLY true，避免 JS 讀取。
SESSION_COOKIE_SAMESITE Lax 或依 deployment 驗證。
CSRF 所有 state-changing admin form 必須有 token。
Session lifetime 建議 8 小時 absolute；重要操作可要求重新輸入密碼。
Login rate limit 例如每 IP/帳號 5 次/15 分鐘，超限暫時拒絕。
Password reset v1 不做 email reset；使用 server CLI reset-admin-
password。
11.2 常見威脅與對策
威脅 情境 對策 驗收
Brute force 掃 /admin/login rate limit + 強密碼 + audit連續失敗觸發 429/暫停。
CSRF 誘導已登入 admin 發
POST
Flask-WTF/CSRF token缺 token 的 POST =
400/403。
XSS 研究摘要輸入 script 預設 autoescape；不用
raw HTML；若有 rich text
必須 sanitize
payload 不執行。
Malicious upload 上傳偽裝程式檔 MIME/extension
allowlist、重新命名、尺寸
上限、圖片重新編碼
非允許類型拒絕。
Session theft cookie 被攔截 HTTPS + Secure +
HttpOnly + SameSite
production cookie flags 正
確。
Data loss container rebuild / host
failure
bind mount + DB-aware
backup + off-host copy
restore drill 通過。
威脅 情境 對策 驗收
Secret leak 把 .env push Git gitignore + deployment
secret management
repo scan 無 secret。
12. SEO 規格
SEO 是 GEO 的基礎層。Google 2026 官方 AI optimization guide 明確指出 generative AI features 仍建立於核心 Search
ranking / quality systems；網站首先要可抓取、可索引、內容有價值且 people-first [S5]。因此本案不把任何「AI SEO
hack」取代基本 SEO。
12.1 Technical SEO
項目 實作規格 驗收
Title 每頁唯一；預設由 entity + Lab 組
成，可在 admin override
不重複、不空白。
Meta description 人物/成果依摘要自動生成，admin 可
override
避免全站同一段。
Canonical 由 server 產生 absolute URL query variations 指向同 canonical。
robots.txt 允許 public；禁止 /admin/；
sitemap 指示
production URL 正確。
sitemap.xml 只收 published canonical pages；
updated_at 作 lastmod
發布/封存後自動反映。
Open Graph title/description/image/url/type社群預覽基本完整。
Semantic HTML header/nav/main/article/section/
footer；H1 唯一且層級合理
HTML audit。
Internal linking Person <-> Research 雙向；首頁導向
核心內容
不存在孤兒 published page。
404/301 slug 變更走 redirects 301；不存在才
404
舊 URL 不造成無意義 404。
Images 有意義圖片 alt、width/height、
responsive sources、lazy loading
無 CLS、無缺 alt。
12.2 Structured Data
Google 建議在可行時使用 JSON-LD，較容易維護 [S6]。但 structured data 必須與使用者看得到的主內容一致，不能用
markup 虛構額外資訊 [S7]。
頁面 Schema.org 類型 重點 properties 注意
全站/首頁 WebSite + Organization；
ResearchOrganization 可
評估
name, url, logo,
parentOrganization,
sameAs
ResearchOrganization 在
schema.org 仍屬較新型
別，必要時保守使用
Organization [S12]。
頁面 Schema.org 類型 重點 properties 注意
教授/人物 Person name, url, image,
affiliation/memberOf,
sameAs, alumniOf
只輸出已公開且有依據的值
[S13]。
期刊/會議成果 ScholarlyArticle headline/name, author,
datePublished, about,
keywords, sameAs/
identifier
只有實際 scholarly article
才使用 [S14]。
一般研究成果 CreativeWork name, description,
author/contributor,
about, keywords,
dateCreated/
datePublished
避免硬套 Article [S15]。
資料集 Dataset name, description,
creator, keywords,
distribution/identifier（有
時）
只有真的公開 dataset 才使
用。
Breadcrumb BreadcrumbList position/name/item 詳細頁可輸出。
12.3 SEO metadata fallback 規則
Person title:
if seo_title_zh exists -> use it
else -> "{name_zh} | {research_focus_short} | NTUST SiPh Lab"
Research title:
if seo_title_zh exists -> use it
else -> "{title_zh} | NTUST SiPh Lab"
Description:
explicit seo_description
-> summary first 140-180 chars
-> research_focus / thesis fallback
-> site default as last resort
13. GEO / AI Search 規格
先釐清 GEO 邊界 Google 官方明確說明：針對 Google AI Overviews / AI Mode，不需要 llms.txt 或特殊 AI markup；
structured data 也不是 AI Search 的必要條件。核心仍是高品質、可抓取、people-first 的 SEO [S5]。因此 SAI 將
GEO 定義為「實體清楚、內容可抽取、來源可驗證、關係可解析」的增強層，而不是排名保證。
13.1 GEO Content Contract
原則 研究室網站的具體實作
Entity clarity 教授、人物、研究成果都有 canonical URL、明確名稱、身
份、日期與關聯。
Fact density 用可驗證的研究題目、方法、工具、結果、年份取代形容
詞堆疊。
Answerable structure 成果頁自然使用 Problem / Method / Results /
Significance，使人類也能快速讀懂。
Sourceability 論文提供 DOI/venue，專案提供 repo/demo，人物提供
ORCID/Scholar 等官方/自有連結。
Freshness 每頁顯示 last updated；資料庫保存 updated_at。
Consistent naming NTUST / National Taiwan University of Science and
Technology、教授中英文名等需統一。
Cross-entity linking Person -> Related outputs；Research -> Contributors；
Alumni -> thesis/outputs。
No hidden AI-only text 給 AI 的資訊必須同時對人可見，避免 hidden content。
13.2 llms.txt / agent-readable files
第三方 AI SEO Skill 常建議 llms.txt 或其他 agent-readable 檔案，例如 coreyhaines31/marketingskills 的 ai-seo Skill 會將
llms.txt 視為非 Google AI 引擎的可選結構化層 [S3]。本案可以在 v1.2 產出 /llms.txt，列出 Lab、Members、Research、
Alumni 的重要 canonical links，但必須標記為「實驗性相容層」，不得寫成 Google 排名必要條件。
# Optional /llms.txt (experimental)
# Not a Google ranking requirement.
# NTUST SiPh Lab
- About: https://<domain>/about
- Current members: https://<domain>/members
- Research outputs: https://<domain>/research
- Alumni: https://<domain>/alumni
# Prefer canonical HTML pages as source of truth.
13.3 GEO 驗收不是「問 ChatGPT 有沒有引用一次」
先驗證 crawl/index：Search Console URL inspection、sitemap、robots、canonical。
驗證 entity content：每個成果頁都有人能回答 Problem/Method/Result/Significance。
驗證 structured data：語法有效、內容與頁面一致；不能把 Rich Results Test 通過等同於一定會顯示 rich result。
建立固定 10-20 組關鍵查詢做人工 baseline，例如「NTUST silicon photonics lab」「楊淳良 矽光子」「NTUST optical
performance monitoring」等；記錄可見度，不承諾排名。
每季檢視 Search Console / referral / AI citation observations，再調整內容，而非每週追逐 GEO hack。
• 
• 
• 
• 
• 
14. GitHub Agent Skills 納入方式
本案採「來源 Skill -> 篩選 -> 固化成專案 Skill」模式。不要讓 Agent 每次直接照遠端最新版自由發揮；遠端 Skill 用來提供原
則，專案內的 SKILL.md 才是實際 contract。
14.1 採用來源
來源 吸收內容 不直接照搬的部分
Anthropic / skills / frontend-design
[S2]
Subject-first、Hero 是 thesis、
Typography/structure/motion 都要
有意圖、避免模板化
「極端美學」只作思考工具；研究室
網站仍以學術可信與長文可讀為優
先。
Vercel / agent-skills / web-design-
guidelines [S16]
把 UI review 變成規則化 audit；每次
review 讀最新 Web Interface
Guidelines
Production 不依賴遠端 fetch；在 CI/
Agent review 時才使用。
Microsoft / skills / frontend-design-
review [S4]
Frictionless / Craft / Trustworthy 三
類評估、Design token、a11y、
responsive
Figma/Storybook 流程非本案必要前
置。
coreyhaines31 / marketingskills / ai-
seo [S3]
AI citation 的內容結構、entity/fact/
citation 思維、跨平台 GEO 視角
Google 部分以 Google 官方文件為
準；llms.txt 只列 optional。
14.2 專案 Skill: skills/siph-lab-web-design/SKILL.md
---
name: siph-lab-web-design
description: Design and review NTUST SiPh Lab public/admin web UI. Use for all templates, CSS,
components and UX changes.
---
# Mission
Build a distinctive academic research site grounded in silicon photonics,
optical communications, measurement traces, waveguides and ring resonators.
Do not ship generic AI/SaaS visual language.
# Before coding
1. State the page's single job and primary audience.
2. Identify real content required for that page.
3. Choose the SiPh-specific visual device used on this page.
4. Reuse project design tokens before creating new values.
# Visual contract
- Deep Navy + Photon Teal/Cyan; accents are sparse.
- Editorial research layout; generous negative space.
- Hero must communicate Lab thesis, not decorative statistics.
- Use waveguide/ring/measurement motifs only when semantically relevant.
- Avoid generic glassmorphism, purple gradients, floating blobs and repeated 3-card grids.
- Motion must support orientation or feedback; support prefers-reduced-motion.
# UX contract
- One obvious primary action per view where possible.
- Public content has clear next links; no dead ends.
- Admin common tasks must be completable without editing code.
- Forms show required/optional state, validation and saved status clearly.
# Accessibility
- Semantic landmarks and heading hierarchy.
- Keyboard reachable controls and visible focus.
- Sufficient contrast.
- Informative images need useful alt text.
- Do not encode meaning by color alone.
# Review gate
Reject if: generic AI aesthetic, hardcoded token sprawl, mobile overflow,
missing focus state, unreadable long-form content, or content hierarchy is unclear.
14.3 專案 Skill: skills/siph-lab-seo-geo/SKILL.md
---
name: siph-lab-seo-geo
description: Enforce technical SEO, structured data and AI-search readability for NTUST SiPh Lab.
---
# Source of truth priority
1. Visible page content and verified Lab facts
2. Google Search official guidance
3. Schema.org definitions
4. Project-specific rules
5. Third-party GEO skills (advisory only)
# Required for every published entity page
- unique canonical URL
- unique title and useful description
- clear H1 and semantic main content
- entity name / role or research type / year
- last updated
- internal links to related entities
- JSON-LD only when it truthfully matches visible content
# Research output contract
Explain Problem, Method, Results, Significance and references/links where known.
Never invent metrics, citations, DOI, affiliations or publication status.
# AI-search rule
Do not claim llms.txt or special AI markup is required for Google AI features.
Treat optional agent-readable files as experimental compatibility layers.
# Publish gate
Reject publish if canonical is broken, page is empty/thin, required factual fields
are missing, structured data contradicts visible text, or the entity is duplicated.
15. 內容管理表單 - 欄位級規格
15.1 Current Member 表單
區塊 欄位 UI 驗證 發布門檻
Identity 中文名 / 英文名 /
slug / status
text + select 中文名 required；
slug unique
name + current
status。
Academic degree / entry yearselect + number year range 建議填。
Research research focus zh/
en / thesis / skills
textarea + tag input至少一語言 research
focus
research focus
required。
Links email public /
ORCID / Scholar /
GitHub / LinkedIn
URL/email inputs 格式檢查 全部 optional。
Photo image + alt zh/en file + text jpg/png/webp；
size limit；有圖必填
alt
照片 optional。
SEO title/description
override
advanced accordion長度提示但不硬截斷可自動 fallback。
Publish status / sort orderselect + number published 前 run
publish validator
所有門檻通過。
15.2 Research Output 表單
區塊 欄位 UI 發布門檻
Identity type/year/title zh/en/slugselect + number + texttype/year/title/slug。
Summary summary zh/en textarea 至少一語言 summary。
Research body problem/method/results/
significance
structured textareas 論文/代表成果建議至少
Method + Results。
Publication venue/date/DOI/external
URL/GitHub
inputs DOI 格式 normalization；
URL validation。
People related lab people +
display author text
multi-select + text 關聯 person 必須存在。
Keywords keywords zh/en tag input 去重/trim。
Media hero image + alt upload 有圖必填 alt。
區塊 欄位 UI 發布門檻
Feature featured / order toggle + number published 才能 featured。
SEO title/description overrideadvanced 可 fallback。
15.3 Alumni 表單
欄位群 差異
Identity 沿用 Person；status=alumni。
Graduation graduation_year 建議 required；degree、thesis title。
Research 保留在學時 research focus / skills / related outputs。
Destination current_affiliation/current_position 僅在確認可公開時
填。
Privacy Admin 須有「不公開就業資訊」的明確選項；空值不顯
示。
15.4 Site Settings 表單
Tab 內容
Lab Identity 中英文名、短名、學校、系所、Logo。
Homepage Hero title/intro/media、featured section 文案。
Professor 若教授採 Person status=faculty，這裡只選 featured
professor；人物資料在 People 編輯。
Join & Contact 招募文案、email、address、map link。
SEO Defaults site suffix、default description、default OG image、
production base URL。
Advanced robots policy、optional llms.txt toggle；需附說明，不讓
管理員誤以為是排名開關。
16. Media / Upload 規格
控制 規格
Allowed types jpg/jpeg/png/webp；必要時 svg 只接受可信內建 asset，
不接受一般 admin 任意 SVG。
Max size 建議原始上傳 8 MB 上限；超出拒絕並提示。
Filename 伺服器生成 UUID/slug-safe 名稱，不使用 user filename
作實際路徑。
Processing 人物照建立固定尺寸 variant；研究圖片保留合理最大寬；
移除不必要 metadata。
Alt text 管理表單有顯著欄位；純裝飾 asset 用空 alt，資訊圖必須
描述資訊用途。
Deletion 被內容引用的檔案不可直接刪；先解除引用或 archive。
Backup uploads 與 DB 同一 backup unit。
17. Accessibility 與 Responsive
面向 驗收規格
Keyboard Tab 可依合理順序到所有互動元件；focus ring 清楚。
Headings 每頁一個主要 H1；不可用 heading 純粹放大字。
Contrast 文字/控制對比達 WCAG AA 目標；設計 review 必須檢查。
Motion prefers-reduced-motion 時停用非必要動態。
Forms label 與 input 正確關聯；error message 指向欄位且有文
字。
Images 資訊圖有 alt 或附近文字等價描述。
Mobile 320px 寬不出現橫向 body scroll；nav/forms/cards 可
用。
Touch 主要 touch targets 不過小，hover 不是唯一訊息來源。
Long text 研究摘要/方法段落控制 line length，手機不塞過寬表格。
18. Performance 規格
首屏 HTML server-rendered，不等待大型 JS bundle 才出內容。
CSS 採少量分檔；production 可 minify / cache bust，但不導入大型前端 framework。
圖片提供 width/height，人物照與成果圖產生 WebP 等合適版本；非首屏 lazy-load。
字體不為美學載入過多 weights；繁中文字體特別注意檔案大小，必要時使用 system fallback / subset。
靜態資產加長 cache；HTML 保持短 cache 或 ETag，發布後可即時更新。
以 Lighthouse/WebPageTest 作驗收工具之一；目標不是只追滿分，而是避免 LCP/CLS/可用性問題。
• 
• 
• 
• 
• 
• 
19. Logging / Error Handling / Health
項目 規格
App log stdout/stderr JSON 或一致格式；Docker 收集。
No secret logging password、session cookie、SECRET_KEY、完整 form
payload 不進 log。
404 提供回首頁/研究/成員的導覽；先查 redirects。
500 public 顯示友善錯誤；詳細 stack 只在 server log。
/healthz 確認 app process alive；可做輕量 DB SELECT 1，不回敏
感資料。
AuditLog 記錄 admin 內容與安全事件；不是 application debug log
的替代品。
20. 測試策略
層級 測試 最低案例
Unit slug / validators / SEO fallback /
schema builder
slug collision、empty metadata、
url normalize。
Model constraints / relationships person-research link、unique
slug、graduate transition。
Route public / auth / admin published 200、draft 404、unauth
admin redirect。
Security CSRF / session / rate limit / upload缺 token 拒絕、惡意 extension 拒
絕。
SEO canonical / sitemap / robots / JSON-
LD
published in sitemap、draft
excluded、schema parses。
Migration Alembic upgrade/downgrade
(where safe)
empty DB -> head；existing seed ->
head。
Backup backup -> restore -> integrity 資料、關聯、圖片 hash 一致。
UI responsive / keyboard / focus /
forms
mobile overflow、keyboard flow。
20.1 必要 acceptance cases
ID 驗收敘述
AC-01 未登入進 /admin -> /admin/login。
AC-02 錯密碼不建立 session；連續錯誤觸發 rate limit。
AC-03 正確帳密登入後 /admin 可見。
AC-04 新增 Current Member，發布後出現在 /members。
AC-05 Person detail 有 canonical、title、description、Person
JSON-LD。
AC-06 將 current 轉 alumni，person id/slug 不變，/members
消失、/alumni 出現。
AC-07 新增 ResearchOutput，關聯 Person 後雙向可導航。
AC-08 ResearchOutput draft 不出現在 public/sitemap。
ID 驗收敘述
AC-09 ResearchOutput published 出現在 /research 與
sitemap。
AC-10 修改 published slug 產生 301 redirect。
AC-11 上傳非法副檔名被拒絕。
AC-12 有圖但無 alt 時，publish validator 警告/阻擋依規格。
AC-13 docker image rebuild 後 DB 與 uploads 不消失。
AC-14 執行 backup + restore drill 後 sqlite integrity_check
pass。
AC-15 手機 320px 無 body horizontal scroll。
AC-16 鍵盤可完成 admin login 與主要表單。
AC-17 structured data 不含頁面看不到的虛構欄位。
AC-18 首頁 H1 唯一，主導覽與 footer 都可使用鍵盤。
AC-19 500 頁不顯示 stack trace。
AC-20 production 啟動使用 Gunicorn，不是 flask run。
AC-21 migration_inventory 中所有 LC 項目都有 source、type、
target、status；不得有空白 target/status。
AC-22 正式 launch 前，Legacy Baseline 必須 100% 為
MIGRATED / APPROVED_REWRITE /
APPROVED_REMOVE；UNRESOLVED = 0。
AC-23 教授姓名、職稱、學歷、六項專長、Email、NTUST 外鏈
與四位碩二生姓名逐字或經批准改寫後可在新站找到。
AC-24 教授圖片/媒體資產有 manifest、checksum 或人工驗證紀
錄；不得因下載困難而靜默遺漏。
AC-25 Google Sites 平台 chrome 被排除，但 Google Calendar
embed 必須有明確 MIGRATED 或 APPROVED_REMOVE 決
策。
AC-26 difference_report 在 cutover 前無未解決缺漏；舊站不得
在 sign-off 前關閉或改成搬遷公告。
21. Cloud Run Production Deployment
正式上線不是把本機 siph_lab.db 複製進 Cloud Run。Cloud Run container filesystem 不是永久儲存，instance 停止後資料
不持久 [S17]。Production migration 的正確做法是：部署相同 Flask image、建立 PostgreSQL schema、匯入 SQLite 資
料、同步媒體到 Cloud Storage，再切換正式網址。
21.1 Production Migration Gate
Gate 必須通過
G1 Feature freeze Local acceptance tests 全通過，內容 schema 不再任意變
動。
G2 DB portability Alembic 在 PostgreSQL staging 從 empty -> head 成功；
核心 CRUD integration test 通過。
G3 Data export SQLite migration package 產生完成，row counts、
checksum、schema revision 有紀錄。
G4 Media export 所有 local uploads 有 object key、checksum、alt/mime
metadata。
G5 Rollback point 保留最後一份 known-good SQLite + uploads snapshot；
舊 Google Sites 尚未關閉。
G6 Legacy No-Loss Gate difference_report.md 無 UNRESOLVED；所有母站內容都
有 migrated/approved decision，且 content sign-off 完
成。
21.2 GCP Resource Baseline
建立/選定 GCP project，啟用 Cloud Run、Artifact Registry、Cloud SQL Admin、Cloud Storage、Secret Manager 所
需 API。
建立 Artifact Registry repository，build/push Flask container image。
建立 Cloud SQL for PostgreSQL instance、database 與專用 DB user；Cloud Run 使用受控 service account 連線。
Google 官方提供 Cloud Run 連 Cloud SQL 與 SQLAlchemy/Python Connector 範例 [S18][S19]。
建立 Cloud Storage bucket，僅授權 Cloud Run service account 必要 object 權限。
SECRET_KEY、DB credential 等正式秘密放 Secret Manager；Cloud Run 可將 Secret Manager secret 提供給 container
[S21]。
21.3 Data / Media Migration
對 Cloud SQL 執行 flask db upgrade，禁止直接從 SQLite schema dump 建 PostgreSQL schema。
執行 import_postgres.py；以穩定 ID/slug 或 migration mapping 保持 Person、ResearchOutput 關聯。
執行 sync_media_to_gcs.py；上傳後驗證 object count 與 checksum。
執行 verify_migration.py：table counts、null/unique constraints、foreign-key relationships、抽樣頁面內容。
1. 
2. 
3. 
4. 
5. 
1. 
2. 
3. 
4. 
21.4 Cloud Run Service
APP_ENV=production
PUBLIC_BASE_URL=https://<production-domain>
DB_BACKEND=postgresql
STORAGE_BACKEND=gcs
GCS_BUCKET=<bucket-name>
# secrets / DB connection values injected by GCP
Cloud Run service 對公開網站允許 public HTTPS；Admin 權限仍由 Flask Login + CSRF + session 保護。
不把 DB、uploads、backup 寫入 container filesystem；暫存檔使用短生命週期 temp path，完成後刪除。
每次 deployment 後執行 /healthz、首頁、人物頁、成果頁與 Admin login smoke test。
21.5 學校網域策略
正式名稱建議向校方申請例如 siph-lab.ntust.edu.tw。Cloud Run 本身提供穩定 HTTPS endpoint；Google 目前對正式
custom domain 的官方首選是 global external Application Load Balancer，Cloud Run 原生 domain mapping 仍屬
Preview/limited availability，不列為本案 production baseline [S22]。在學校 DNS 尚未完成前，可先用 run.app URL 做
staging/驗收，避免阻塞開發。
21.6 更新與 Rollback
先跑 migration/test；DB schema 變更使用 backward-compatible migration 優先。
部署新 Cloud Run revision，先以 revision URL / traffic split 做 smoke test。
通過後才切 100% traffic；失敗則把 traffic 切回上一個 known-good revision。
資料 migration 需有獨立 rollback/forward-fix 計畫；不能只依賴 container rollback 還原資料庫。
• 
• 
• 
1. 
2. 
3. 
4. 
22. Google Sites -> Cloud Run 正式站遷移與零遺漏規
範
Legacy Content Preservation Rule（硬性）：現行母站中所有經確認有效的文字、教授資訊、研究專長、成員資
料、圖片、外部連結與嵌入內容，均須納入 migration inventory，並在新網站有明確對應。除非教授/管理者明確指定
刪除、合併或更新，Agent 不得自行省略。任何未決項目都會阻擋正式上線。
22.1 Migration Status Model
Status 定義 是否允許 Launch
DISCOVERED 已在母站發現，但尚未分類。 否
REVIEW_REQUIRED 用途或有效性需教授/管理者判斷，例
如 calendar/embed。
否
MIGRATED 已完整搬到新站並通過內容/連結/媒
體驗證。
是
APPROVED_REWRITE 原內容已更新或合併，且有核准紀錄
與新位置。
是
APPROVED_REMOVE 管理者明確批准不搬，記錄理由、核
准人與日期。
是
UNRESOLVED 缺檔、缺映射、無法判斷或驗證失
敗。
否；阻擋 Launch
22.2 migration_inventory.csv 最低欄位
欄位 必要 說明
legacy_id 是 LC-001...固定 ID。
source_url 是 原 Google Sites URL 或資產 URL。
captured_at 是 ISO-8601 抓取/盤點時間。
content_type 是 text / person / image /
external_link / embed / section。
source_value 是 原始可見文字或資產描述。
target_entity 是 SiteSetting / Person /
ResearchOutput / Page / Asset。
target_url_or_field 是 新站 URL 或 DB 欄位。
欄位 必要 說明
status 是 上述 migration status。
approval 條件式 rewrite/remove 時必須填核准人、日
期、理由。
verification 是 exact/text match、HTTP check、
checksum 或人工核對結果。
22.3 Old -> New Mapping 基準
母站內容群 新站位置 資料模型 驗收方式
Lab 名稱 全站 Header / Home /
metadata
SiteSetting 文字比對 + metadata
check
教授圖片、姓名、職稱、學
歷、Email
Home professor block /
Professor detail
Person + Asset 欄位比對 + image manifest
六項專長 Research Focus /
Professor detail
Person expertise /
SiteSetting taxonomy
六項集合比對，不可漏項
NTUST Website 外鏈 Professor external linksPerson external_url HTTP/人工連結驗證
四位碩二生 /members + detail Person(status=current)四筆姓名/身份存在
Google Calendar embed依管理者決策：保留於適當
頁面或批准移除
Page embed / approved
removal record
不得停留
REVIEW_REQUIRED
22.4 Migration Runbook
Freeze snapshot：上線遷移前重新抓母站，保存 source URL、文字 snapshot、截圖/資產清單與抓取日期。
Classify：區分 Lab content、asset、external link、embed 與 Google Sites platform chrome；不確定的項目只能標
REVIEW_REQUIRED。
Inventory：每個內容單元建立 legacy_id；禁止「看起來不重要」就不登錄。
Map：指定新 DB entity、欄位或 public URL。若一筆原文拆成多處顯示，也要記錄所有 target。
Migrate：透過 seed/import 或 Admin 寫入；圖片/media 產生 checksum/object key；外鏈保留實際 URL。
Automated diff：產生 difference_report.md，檢查缺項、空 target、文字集合差異、broken links、missing assets。
Human QA：教授/管理者核對姓名、職稱、學歷、專長、Email、成員與特殊 embed；所有 rewrite/remove 都留下批准
紀錄。
No-Loss Gate：UNRESOLVED 必須為 0，AC-21~AC-26 全通過，才可進 Cloud/domain cutover。
Cutover：Cloud Run/Cloud SQL/Cloud Storage 驗收完成、正式網域鎖定後，再把舊 Google Sites 改成搬遷說明。
22.5 Difference Report 規則
文字：教授姓名、Email、六項專長、四位成員姓名採 exact/set comparison；有更新時必須連到 approval record。
圖片：不能只檢查「有一張圖」；必須核對來源資產、檔名/object key、checksum 或人工 visual sign-off。
外鏈：保留原 URL；若換到新版官方 URL，記為 APPROVED_REWRITE 並驗證可到達。
1. 
2. 
3. 
4. 
5. 
6. 
7. 
8. 
9. 
• 
• 
• 
Embed：不得因爬蟲無法讀取就當成不存在；任何 iframe/embed 都要在 media/embed manifest 中有一筆。
Platform chrome：只在 exclusion list 中排除，避免把 Google Sites 自帶 UI 誤當 Lab 內容。
22.6 Content Sign-off
content_signoff.md 至少記錄：baseline snapshot 日期、inventory 總筆數、MIGRATED 數、APPROVED_REWRITE 數、
APPROVED_REMOVE 數、UNRESOLVED 數、核對人、核對日期。正式 launch 的必要條件為 UNRESOLVED = 0。
不可自動腦補：母站沒有的研究成果、畢業生、英文名、研究題目、去向等資料，Agent 不得從網路推測後直接發布。
可以另外蒐集候選資料，但 publication 必須由 Lab/教授確認。
• 
• 
23. 開發階段與 Definition of Done
階段 交付 DoD
P0 Foundation Flask factory、config、Docker、
SQLite、migrations、admin auth
本機 compose 可跑；login 安全。
P1 Data/CMS Person、ResearchOutput、
Settings、Admin CRUD
新增/編輯/發布/graduate flow 通
過。
P2 Public IA Home/Members/Research/Alumni/
Detail
內容由 DB render；responsive
baseline。
P3 Design/SEO/GEO Design System、metadata、JSON-
LD、sitemap
視覺/a11y/SEO acceptance 通過。
P4 Portability Repository boundary、storage
adapter、PostgreSQL migration
tests
SQLite + PostgreSQL test matrix 通
過。
P5 Migration tooling export/import/media sync/verify
scripts
至少完成一次 dry-run round trip。
P6 Cloud staging Cloud Run + Cloud SQL + Cloud
Storage
revision restart 後資料持久；Admin
CRUD 通過。
P7 Domain/Launch 學校網域、canonical、Search
Console、舊站搬遷
正式網址與 rollback checklist 完成。
23.1 Agent 開發規則
每次功能修改先讀 docs/SAI.md 與兩個 project SKILL.md。
本機預設 SQLite；不得為了「像 production」而要求開發者先建立 Cloud SQL。
不得在 business/service/route 層寫 SQLite-only SQL；DB-specific 處理必須隔離。
所有 schema 變更建立 Alembic migration，且要測 SQLite 與 PostgreSQL。
所有媒體存取透過 StorageBackend；template/model 不可假設 /uploads 一定是本機檔案。
Cloud Run production 禁止永久寫入 local filesystem [S17]。
新增 public page 必須同時處理 title、description、canonical、sitemap inclusion、structured data applicability。
所有 Admin mutation 必須 auth + CSRF + server validation；沒有真實資料時不可虛構內容。
任何 legacy migration 變更必須同步更新 migration_inventory.csv 與 content_mapping.csv；不得直接刪除母站內容而不
留下批准紀錄。
Agent 若無法取得圖片/embed 原始資產，必須建立 UNRESOLVED issue，而不是用空白、任意替代圖或靜默略過。
• 
• 
• 
• 
• 
• 
• 
• 
• 
• 
24. 風險與取捨
風險 可能性/衝擊 對策 驗收/升級條件
SQLite -> PostgreSQL 行為
差異
中/高 SQLAlchemy portability
contract；雙 DB CI；
migration dry run
Cloud staging CRUD +
migration verification 全
通過才 launch。
Cloud Run ephemeral
filesystem
高/高 正式 DB=Cloud SQL；
media=Cloud Storage；禁
止永久 local write [S17]
重部署/restart 後資料與圖
片仍存在。
Cloud SQL 固定成本 高/中 開發期完全 local；只在準
備上線時建立；啟用
budget alert
若成本不可接受，另做 ADR
評估其他 managed
PostgreSQL，不私自改資
料模型。
學校 DNS/網域行政時間中/中 Cloud Run run.app 先
staging；網域不阻塞開發
正式 launch 前 canonical/
public base URL 必須鎖
定。
Cloud Run custom
domain 路徑
中/中 production baseline 依
Google 建議採 external
Application Load
Balancer；不依賴 Preview
domain mapping [S22]
校方 DNS 與 TLS 驗證完
成。
內容維護停滯 中/高 後台極簡、publish
checklist、dashboard 提
醒
半年無更新則檢討內容責
任。
資料隱私 中/高 alumni destination opt-in/
confirmed only
需要時建立正式 privacy/
consent 流程。
母站內容遺漏/誤刪 中/高 Legacy inventory + old/
new mapping + difference
report + content sign-off
UNRESOLVED=0 且
AC-21~26 通過才可
cutover。
25. SAI v1.2 最終範圍鎖定
v1.2 Core：一個 Flask/Jinja application、一個單一管理帳號、三類核心公開內容（在學碩士生 / 研究成果 / 畢業
生）、SiPh 專屬 Design System、SEO/GEO 規格；開發與驗收使用 SQLite + local uploads，正式上線目標為 Cloud
Run + Cloud SQL PostgreSQL + Cloud Storage。
Legacy Preservation：母站確認有效內容零遺漏；任何刪除/改寫必須有批准紀錄。
這不是兩套網站，也不是重寫兩次。程式碼、models、routes、templates、Admin UX 都是同一套；環境差異只允許出現在
configuration、database connection、storage adapter 與 deployment/migration tooling。未來若新增 News、
Equipment、multi-admin 或 API，必須另開 ADR，不得破壞此核心邊界。
附錄 A - Route Matrix
Method Path Auth Purpose Result
GET / Public Home 200
GET /about Public About 200
GET /members Public current people list200
GET /alumni Public alumni list 200
GET /people/<slug> Public person detail 200/301/404
GET /research Public research index 200
GET /research/<slug> Public research detail 200/301/404
GET /join Public join/contact 200
GET /sitemap.xml Public sitemap 200
GET /robots.txt Public robots 200
GET /healthz Public health 200/503
GET /admin/login Anonymous only login form 200/302
POST /admin/login Anonymous only authenticate 302/4xx
POST /admin/logout Admin logout 302
GET /admin Admin dashboard 200
GET/POST /admin/people/newAdmin create person 200/302
GET/POST /admin/people/
<id>/edit
Admin edit person 200/302
POST /admin/people/
<id>/graduate
Admin current -> alumni 302/4xx
GET/POST /admin/research/
new
Admin create output 200/302
GET/POST /admin/research/
<id>/edit
Admin edit output 200/302
POST /admin/research/
<id>/publish
Admin publish 302/4xx
Method Path Auth Purpose Result
POST /admin/research/
<id>/archive
Admin archive 302/4xx
GET/POST /admin/settings Admin site settings 200/302
GET/POST /admin/passwordAdmin change password200/302
附錄 B - 建議 Environment Variables
# .env.local
APP_ENV=local
SECRET_KEY=<local-secret>
PUBLIC_BASE_URL=http://localhost:8000
DATABASE_URL=sqlite:////app/instance/siph_lab.db
STORAGE_BACKEND=local
UPLOAD_DIR=/app/uploads
MAX_CONTENT_LENGTH=8388608
SESSION_COOKIE_SECURE=false
# Cloud Run environment (non-secret examples)
APP_ENV=production
PUBLIC_BASE_URL=https://<production-domain>
DB_BACKEND=postgresql
STORAGE_BACKEND=gcs
GCS_BUCKET=<bucket-name>
SESSION_COOKIE_SECURE=true
LOG_LEVEL=INFO
# SECRET_KEY / DB password etc. -> Secret Manager
# Never store admin plaintext password in env or repository.
附錄 C - Publish Checklist
類型 Publish 前檢查
Person 姓名正確；status 正確；research focus 有內容；照片有
alt；外鏈可用；公開個資已確認；canonical/slug
unique。
Research title/type/year/summary；方法/結果符合內容；DOI/
URL 真實；related people 正確；hero alt；SEO fallback
可讀。
Alumni graduation year；thesis/研究方向；current affiliation
only if confirmed；仍保留 related outputs。
Site settings Lab/教授/聯絡資訊正確；Hero 不誇大；default OG/SEO
完整。
Legacy content checklist
重新抓取母站 snapshot，日期與來源 URL 已記錄。
LC-001~LC-019 全部存在於 migration inventory。
教授基本資料、六項專長、Email、外鏈、圖片、四位成員皆有新站 target。
Google Calendar embed 有明確保留/移除決策。
Google Sites platform chrome 僅依 exclusion list 排除。
difference_report 無 UNRESOLVED。
所有 APPROVED_REWRITE / APPROVED_REMOVE 有核准紀錄。
content_signoff 完成後才允許舊站改成搬遷公告。
Cloud launch checklist
PostgreSQL migrations from empty -> head 通過。
SQLite export / PostgreSQL import 驗證 row counts、FK、slug、timestamp。
Media object count/checksum 與 Cloud Storage 同步報告完成。
Cloud Run restart/new revision 後資料持久。
Secret Manager 無 plaintext secret 落入 image/repo。
PUBLIC_BASE_URL、canonical、sitemap、Search Console 使用正式網域。
舊 Google Sites 顯示搬遷說明與新網址。
• 
• 
• 
• 
• 
• 
• 
• 
• 
• 
• 
• 
• 
• 
• 
附錄 D - 來源與依據
[S1] NTUST SiPh Lab 現行 Google Sites - 2026-08-14 盤點：教授楊淳良基本資訊、研究專長、聯絡資訊與四位碩二生姓
名。 Source
[S2] Anthropic - frontend-design Skill - Distinctive subject-grounded frontend design；Hero as thesis；typography/
structure/motion 必須有意圖。 Source
[S3] coreyhaines31/marketingskills - ai-seo - AI search / citation-oriented content structuring；其中 llms.txt 等非
Google 層建議視為 advisory。 Source
[S4] Microsoft - frontend-design-review Skill - UI review 的 frictionless / craft / trustworthy、design system、
accessibility 與 responsive 原則。 Source
[S5] Google Search Central - Optimizing for generative AI features - Google AI Search 仍依核心 SEO；不需要特殊 AI
markup 或 llms.txt；structured data 非 AI 必要條件。 Source
[S6] Google Search Central - Structured data introduction - Google 在可行時建議 JSON-LD，較容易維護。 Source
[S7] Google Search Central - General structured data guidelines - Structured data 必須代表頁面主內容、不能誤導或
描述使用者看不到的內容。 Source
[S8] Flask official documentation - Production deployment/security：不要用 development server；檢視 cookie 與
security headers。 Source
[S9] Docker official documentation - Volumes - Persistent data stores 與 Compose volume/bind mount 行為。 
Source
[S10] SQLite official documentation - WAL - WAL 模式的工作方式、並行與 WAL file 注意事項。 Source
[S11] SQLite official documentation - Online Backup API - 建立一致資料庫 snapshot 的官方備份機制。 Source
[S12] Schema.org - ResearchOrganization - ResearchOrganization 類型與目前 schema.org 狀態。 Source
[S13] Schema.org - Person - Person 的 alumniOf、affiliation/sameAs 等實體屬性。 Source
[S14] Schema.org - ScholarlyArticle - 學術論文的結構化類型。 Source
[S15] Schema.org - CreativeWork - 一般研究成果可保守映射的通用 CreativeWork。 Source
[S16] Vercel - web-design-guidelines Skill - 以規則化方式 review Web Interface Guidelines；適合作為 Agent QA 輔助。
Source
[S17] Google Cloud Run - Container runtime contract - Container writable filesystem is in-memory / non-persistent
when an instance stops. Source
[S18] Google Cloud SQL for PostgreSQL - Connect from Cloud Run - Official Cloud Run to Cloud SQL connection
guidance. Source
[S19] Google Cloud SQL - Python Connector + SQLAlchemy sample - Official SQLAlchemy connection example for
PostgreSQL. Source
[S20] Google Cloud Storage - Python upload object sample - Official object upload/client pattern. Source
[S21] Google Cloud Run - Configure secrets - Secret Manager integration for Cloud Run services. Source
[S22] Google Cloud Run - Mapping custom domains - Google recommends a global external Application Load
Balancer for production custom domains; direct Cloud Run domain mapping remains Preview/limited availability as of
2026-07-22. Source
附錄 E - 開發交付清單
Source code repository + pinned dependencies.
Dockerfile + local docker-compose.yml.
Alembic migrations verified on SQLite and PostgreSQL.
Admin create/reset CLI.
Google Sites verified-content seed script.
StorageBackend local/GCS implementations.
SQLite backup/restore + migration export package.
PostgreSQL import + migration verification report.
Cloud Storage media sync script + checksum report.
Cloud Run deployment/migration/rollback runbook.
Unit/integration/security/SEO/a11y/DB-portability tests.
Project Skills: siph-lab-web-design + siph-lab-seo-geo.
Non-developer content maintenance guide.
Acceptance checklist signed before domain cutover.
Legacy Google Sites source snapshot + migration inventory.
Old-to-new content mapping + media/embed manifest.
Automated difference report + unresolved-zero gate.
Professor/administrator content sign-off record before cutover.
• 
• 
• 
• 
• 
• 
• 
• 
• 
• 
• 
• 
• 
• 
• 
• 
• 
• 