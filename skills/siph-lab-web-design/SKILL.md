---
name: siph-lab-web-design
description: Design and review NTUST SiPh Lab public/admin web UI. Use for all templates, CSS, components and UX changes.
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

---

# 本專案的落實對照（非 SAI 原文，實作補充）

以下記錄本 repo 中「上述規則實際落在哪裡」，讓 review 時可以直接查證。
修改對應檔案時必須回來更新本節，否則本 Skill 會退化成無法驗證的口號。

## Design tokens

`app/static/css/tokens.css` 是唯一的 token 來源，值與 SAI §6.2 一致。

| Token | 值 | 用途 |
| --- | --- | --- |
| `--color-navy` | `#10233D` | Hero、深色區塊底 |
| `--color-photon` | `#00A6A6` | 連結、焦點、強調 |
| `--color-photon-bright` | `#2ED6D6` | 細線幾何、hover |
| `--color-surface-muted` | `#F2F6F8` | 交替區塊底 |
| `--content-max` | `1180px` | 內容最大寬 |

新增顏色前必須先確認無法以既有 token 表達（Review gate 的
「hardcoded token sprawl」）。

## SiPh 視覺裝置

| 裝置 | 實作位置 | 語意 |
| --- | --- | --- |
| 晶片佈局細線 | `.hero-grid-backdrop`（main.css） | Hero 背景，暗示晶片版圖 |
| 微環標記 | `.ring-marker` | 研究方向清單的項目符號 |
| 量測軌跡底線 | `.trace-underline` | section 標題底線 |

這些只在語意相關處使用（Visual contract 第 4 點）。裝飾性使用會被拒絕。

## 結構多樣性

SAI §6.3 禁止「每個 section 都是三張等寬卡片」。首頁各區塊刻意採用不同結構：

- 研究方向 → `focus-item` 清單（含 ring-marker）
- 代表成果 → `card-grid--wide`
- 研究團隊 → `person-card` grid
- 研究室事實 → `meta-list` 定義列表
- 畢業生 → `link-chip` 橫向列

## 導覽的無 JS 決策

主導覽不使用漢堡選單或 `<details>`：只有 6 個短標籤，320px 下換行成兩列即可。
理由完整記錄於 `app/templates/public/base.html` 的註解。
**不得**改為需要 JavaScript 才能展開的形式（違反 UX contract 與 SAI §18）。

## Accessibility 落點

- skip link：`base.html` 的 `.skip-link`，為第一個可聚焦元素
- landmarks：`header` / `nav[aria-label]` / `main#main-content` / `footer`
- 目前頁籤：`aria-current="page"`
- 每頁單一 `<h1>`：由子模板提供
- section 標題關聯：`aria-labelledby`
- 圖片 alt：有圖必填，由 `PublishValidator` 在發布時強制

## 自動化 review gate

`tests/test_a11y.py` 把本 Skill 的部分規則變成可執行檢查
（landmark、H1 唯一性、alt、focus 樣式、觸控尺寸）。
新增 UI 規則時應同步加入該檔，讓 gate 不依賴人工記憶。
