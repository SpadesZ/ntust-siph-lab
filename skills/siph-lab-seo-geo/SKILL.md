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

---

# 本專案的落實對照（非 SAI 原文，實作補充）

## metadata 產生路徑

所有頁面的 SEO metadata 由 `app/services/seo_service.py` 的 `PageMeta`
產生，模板只負責輸出。**不得**在模板或 route 中自行組 title/description
字串——那會造成「部分頁面有、部分沒有」。

fallback 規則（SAI §12.3）實作於 `SEOService`：

```
Person title:   seo_title_zh → "{name_zh} | {research_focus_short} | NTUST SiPh Lab"
Research title: seo_title_zh → "{title_zh} | NTUST SiPh Lab"
Description:    seo_description → summary 前 140-180 字
                → research_focus / thesis → 站台預設
```

canonical 一律取自 `PUBLIC_BASE_URL` 而非 request 的 Host header。
這是刻意的：Host header 可被偽造，且反向代理下不可靠。

## JSON-LD 型別映射

由 `app/services/schema_service.py` 決定，對照 SAI §12.2：

| 內容 | 型別 | 條件 |
| --- | --- | --- |
| 全站 | `WebSite` + `Organization` | 一律輸出 |
| 人物 | `Person` | 已發布且欄位有事實依據 |
| journal / conference | `ScholarlyArticle` | 僅限真正的學術論文 |
| project / prototype / simulation / other | `CreativeWork` | 保守映射 |
| dataset | `Dataset` | 僅限真的公開 dataset |
| 詳細頁 | `BreadcrumbList` | 與可見麵包屑同一份資料 |

`ResearchOrganization` 目前**不使用**（SAI §12.2 [S12]：該型別較新，
必要時保守採用 `Organization`）。

## AC-17 的落實

structured data 不得包含頁面看不到的內容。做法：
麵包屑的 JSON-LD 與可見 `<nav>` 使用**同一份** `meta.breadcrumbs` 資料，
不各自組裝。人物/成果的 JSON-LD 欄位一律取自已渲染到頁面上的模型欄位。

`json_ld()` 全域函式（`app/__init__.py`）負責序列化並把 `<` 轉為 `<`，
防止內容提前關閉 `</script>`。

## llms.txt 的邊界

- 預設**關閉**（`ENABLE_LLMS_TXT=false`），關閉時 `/llms.txt` 回 404
- 後台開關附說明文字，明確標示為實驗性相容層
- `.env.example` 註明「不是 Google 排名的必要條件，也不保證任何效果」

**禁止**在任何文件、UI 文案或註解中把 llms.txt 描述為 Google AI
的必要條件（SAI §13.2 明文禁止）。

## 不得虛構（與 ADR-011 的關係）

本 Skill 的「Never invent metrics, citations, DOI, affiliations or
publication status」與 SAI §2.3、§23.1、ADR-011 是同一條規則的不同面向。

實務判準：**若一項事實無法指向母站內容、Lab 提供的資料或可驗證的
官方來源，就不得發布。** 沒有資料時正確做法是留空並在 inventory 標記
待補，而不是從網路搜尋結果推測填入。

`scripts/seed_from_google_sites.py` 已實作此原則：母站不存在研究成果
與畢業生資料時，明確拒絕產生，並在輸出中說明原因。

## 自動化檢查

`tests/test_seo.py` 涵蓋 canonical、title 唯一性、description、
sitemap 收錄/排除、robots、JSON-LD 可解析性與型別映射。
`scripts/smoke_cloud.py` 在部署後檢查 canonical 與 sitemap 是否
指向正式網域——這是 cutover 最常見且最安靜的疏漏。
