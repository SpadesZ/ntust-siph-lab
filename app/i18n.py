# ============================================================
# NTUST SiPh Lab - Bilingual (zh-Hant / en) Support
#
# 上下游：
#   create_app() -> init_i18n(app)
#       -> before_request  決定本次請求的語言（g.lang）
#       -> context_processor 注入 t / localized / lang_url 給所有 template
#   services/seo_service.py -> canonical / hreflang 依語言分流
#
# 檔案路徑：
#   app/i18n.py
#
# 建立日期：2026-08-16
# 版本：v1.0
#
# 模組定位與責任邊界：
#   本檔是「語言」這件事的唯一收斂點。介面字串、語言判定、
#   語言切換網址、內容欄位的語言挑選，全部在這裡決定。
#
#   責任邊界（不得做的事）：
#     - 不得存取資料庫。
#     - 不得翻譯「內容」—— 內容的英文來自 DB 的 *_en 欄位，
#       由管理者填寫。本檔只翻譯介面字串（導覽、按鈕、標籤）。
#     - 不得在缺英文時自行生成英文內容（SAI §2.3）。
#
# 為什麼是 query parameter 而不是 /en/ 路徑前綴：
#   SAI §4.2 建議「若啟用完整雙語，建議 /zh/ 與 /en/ 形成對稱路由」，
#   但同一節緊接著規定「若翻譯不完整，不建立假的 hreflang 對應頁」。
#
#   本站的實際英文覆蓋率（2026-08-16 實測）：
#     research_outputs  title_en 9/9、summary_en 9/9   -> 完整
#     people            name_en 1/5、research_focus_en 0/5 -> 不完整
#     site_settings     多數 *_en 未填                   -> 不完整
#
#   也就是說「英文版網站」目前並不存在，存在的是「英文介面 +
#   部分英文內容」。若為此建立 /en/ 對稱路由，等於對搜尋引擎
#   宣告一組其實內容重複的英文頁面 —— 那正是 §4.2 禁止的事。
#
#   因此採 ?lang=en：它是「同一份文件的閱讀模式」，不是另一份文件。
#   canonical 一律指向中文網址（見 seo_service），
#   hreflang 只在該頁「雙語內容都齊全」時才輸出（見 page_is_bilingual）。
#   等 people / settings 的 *_en 補齊後，再改為 /en/ 路由並開啟 hreflang。
#
# 已知限制與禁止事項：
#   1. 缺英文的欄位一律回退中文，且必須以 lang 屬性標記回退
#      （WCAG 3.1.2）—— 不得讓螢幕閱讀器以英文腔唸中文。
#   2. 禁止把 UI 字串散落在 template；一律走 t()。
#
# 驗證方式：
#   pytest tests/test_i18n.py
# ============================================================

from __future__ import annotations

import re

from flask import g, request, session, url_for

#: CJK 統一表意文字。只用來判斷「這段是不是中文」（見 text_lang）。
_CJK_RE = re.compile(r"[一-鿿]")

#: 支援的語言。zh 為預設與 fallback。
SUPPORTED_LANGS = ("zh", "en")
DEFAULT_LANG = "zh"

#: 語言代碼 -> <html lang> 的值。
HTML_LANG = {"zh": "zh-Hant-TW", "en": "en"}

#: session 中記住語言偏好的 key。
_SESSION_KEY = "lang"

#: 「英文版刻意留空」的字串。
#:
#: 中文的量詞（位、筆）與「共」在英文沒有對應詞，硬填任何字
#: 都會讓英文變得不自然（"Total 9 outputs" 讀起來像報表）。
#: 這些 key 以空字串表示「這個位置在英文不出現任何東西」，
#: 與「忘記翻譯」是兩件事 —— 分開記錄，測試才能只擋後者。
INTENTIONALLY_EMPTY_EN = frozenset({"count_people", "outputs_count_prefix"})

#: 介面字串。只放「介面」，不放內容。
#:
#: 維護規則：
#:   1. 新增 key 時兩種語言都必須填，缺一不可 ——
#:      tests/test_i18n.py::test_no_missing_translations 會擋下遺漏。
#:      刻意留空的請登記到 INTENTIONALLY_EMPTY_EN。
#:   2. 需要代入數值的字串一律用 %(name)s 具名佔位符，不要用位置參數 ——
#:      中英文的語序不同（「%(year)s 年畢業」/「Class of %(year)s」），
#:      位置參數會在其中一種語言錯位。
STRINGS: dict[str, dict[str, str]] = {
    # --- 全站外框 ---
    "skip_to_content": {"zh": "跳至主要內容", "en": "Skip to main content"},
    "main_nav": {"zh": "主導覽", "en": "Main navigation"},
    "breadcrumb": {"zh": "麵包屑", "en": "Breadcrumb"},
    "ntust_emblem_alt": {
        "zh": "國立臺灣科技大學校徽",
        "en": "National Taiwan University of Science and Technology emblem",
    },
    "lab_logo_alt": {"zh": "標誌", "en": "logo"},
    "contact_and_links": {"zh": "聯絡與連結", "en": "Contact & Links"},
    "ntust_official_page": {"zh": "NTUST 官方頁面", "en": "NTUST official page"},
    "switch_language": {"zh": "切換語言", "en": "Switch language"},
    "lang_name_zh": {"zh": "中文", "en": "中文"},
    "lang_name_en": {"zh": "English", "en": "English"},
    # --- 導覽項目 ---
    "nav_home": {"zh": "首頁", "en": "Home"},
    "nav_about": {"zh": "關於", "en": "About"},
    "nav_members": {"zh": "研究成員", "en": "Members"},
    "nav_research": {"zh": "研究成果", "en": "Research"},
    "nav_alumni": {"zh": "畢業生", "en": "Alumni"},
    "nav_join": {"zh": "加入我們", "en": "Join Us"},
    # --- 首頁 ---
    "home_browse_research": {"zh": "瀏覽研究成果", "en": "Browse research"},
    "home_meet_team": {"zh": "認識研究團隊", "en": "Meet the team"},
    "research_focus": {"zh": "研究方向", "en": "Research Focus"},
    "focus_english_names": {"zh": "研究方向英文名稱 →", "en": "Topic details →"},
    "selected_work": {"zh": "代表研究成果", "en": "Selected Work"},
    "all_outputs": {"zh": "全部成果 →", "en": "All outputs →"},
    "research_team": {"zh": "研究團隊", "en": "Research Team"},
    "all_members": {"zh": "全部成員 →", "en": "All members →"},
    "lab_proof": {"zh": "可驗證事實", "en": "Verifiable Facts"},
    "alumni_preview": {"zh": "畢業生", "en": "Alumni"},
    "all_alumni": {"zh": "全部畢業生 →", "en": "All alumni →"},
    # --- 關於 ---
    "about_title": {"zh": "關於", "en": "About"},
    "principal_investigator": {"zh": "指導教授", "en": "Principal Investigator"},
    "affiliated_university": {"zh": "所屬學校", "en": "University"},
    "department": {"zh": "系所", "en": "Department"},
    "job_title": {"zh": "職稱", "en": "Title"},
    "education": {"zh": "學歷", "en": "Education"},
    "email": {"zh": "Email", "en": "Email"},
    "official_page": {"zh": "官方頁面", "en": "Official page"},
    "view_full_profile": {"zh": "查看完整個人頁面 →", "en": "View full profile →"},
    "related_outputs_link": {"zh": "相關成果 →", "en": "Related outputs →"},
    "methods_and_facilities": {"zh": "研究方法與設備", "en": "Methods & Facilities"},
    "focus_not_public": {"zh": "研究方向尚未公開", "en": "Research focus not yet published"},
    # --- 成員 ---
    "members_title": {"zh": "研究成員", "en": "Research Members"},
    "members_intro": {
        "zh": "%(lab)s 目前的研究團隊。",
        "en": "The current research team at %(lab)s.",
    },
    "graduate_students": {"zh": "在學研究生", "en": "Graduate Students"},
    "no_current_members": {"zh": "目前沒有在學成員資料", "en": "No current member records"},
    "count_people": {"zh": "位", "en": ""},
    #: 區塊標題後面的人數。中文帶量詞與全形括號，英文只有數字。
    "count_people_paren": {"zh": "（%(n)s 位）", "en": "(%(n)s)"},
    # --- 畢業生 ---
    "alumni_title": {"zh": "畢業生", "en": "Alumni"},
    "alumni_intro": {
        "zh": "%(lab)s 歷屆畢業生與其論文題目，依畢業年度排列。",
        "en": "Alumni of %(lab)s and their thesis titles, ordered by graduation year.",
    },
    "alumni_empty_title": {"zh": "畢業生資料尚未建立", "en": "Alumni records not yet compiled"},
    "alumni_empty_body": {
        "zh": "此頁將於研究室提供歷屆畢業生名單與論文題目後更新。",
        "en": "This page will be updated once the lab provides the alumni list and thesis titles.",
    },
    "year_unspecified": {"zh": "年度未標示", "en": "Year not specified"},
    "degree": {"zh": "學位", "en": "Degree"},
    "thesis_title": {"zh": "論文題目", "en": "Thesis"},
    "research_topic": {"zh": "研究主題", "en": "Research topic"},
    # --- 研究成果 ---
    "research_title": {"zh": "研究成果", "en": "Research Outputs"},
    "research_intro": {
        "zh": "%(lab)s 的期刊論文與會議論文，依年份排列，可依類型、年份或關鍵字篩選。",
        "en": "Journal and conference papers from %(lab)s, ordered by year "
              "and filterable by type, year or keyword.",
    },
    "filter_research": {"zh": "研究成果篩選", "en": "Filter research outputs"},
    "output_type": {"zh": "成果類型", "en": "Type"},
    "all_types": {"zh": "全部類型", "en": "All types"},
    "year": {"zh": "年份", "en": "Year"},
    "all_years": {"zh": "全部年份", "en": "All years"},
    "keyword": {"zh": "關鍵字", "en": "Keyword"},
    "keyword_placeholder": {"zh": "標題、摘要或關鍵字", "en": "Title, summary or keyword"},
    "apply_filter": {"zh": "套用篩選", "en": "Apply"},
    "clear_filter": {"zh": "清除", "en": "Clear"},
    "outputs_count_prefix": {"zh": "共 ", "en": ""},
    "outputs_count_suffix": {"zh": " 筆成果", "en": " outputs"},
    "no_matching_outputs": {"zh": "沒有符合篩選條件的成果", "en": "No outputs match these filters"},
    "adjust_or": {"zh": "請調整篩選條件，或", "en": "Adjust the filters, or"},
    "view_all_outputs": {"zh": "查看全部研究成果", "en": "view all research outputs"},
    "no_outputs_published": {"zh": "尚未發布研究成果", "en": "No research outputs published yet"},
    "back_to_research": {"zh": "← 返回研究成果", "en": "← Back to research"},
    # --- 成果詳細 ---
    "summary": {"zh": "摘要", "en": "Summary"},
    "research_question": {"zh": "研究問題", "en": "Research question"},
    "method": {"zh": "研究方法", "en": "Method"},
    "key_results": {"zh": "主要結果", "en": "Key results"},
    "significance": {"zh": "貢獻與限制", "en": "Significance & limitations"},
    "output_info": {"zh": "成果資訊", "en": "Output details"},
    "references_links": {"zh": "參考與連結", "en": "References & links"},
    "lab_contributors": {"zh": "研究室參與成員", "en": "Lab contributors"},
    "keywords": {"zh": "關鍵字", "en": "Keywords"},
    # --- 人物 ---
    "related_outputs": {"zh": "相關研究成果", "en": "Related research outputs"},
    "no_related_outputs": {"zh": "尚未有已發布的研究成果", "en": "No published research outputs yet"},
    "view_all_n_outputs": {"zh": "查看全部 %(n)s 筆成果 →", "en": "View all %(n)s outputs →"},
    "back_to_members": {"zh": "← 返回研究成員", "en": "← Back to members"},
    "back_to_alumni": {"zh": "← 返回畢業生列表", "en": "← Back to alumni"},
    "methods_and_tools": {"zh": "研究方法與工具", "en": "Methods & tools"},
    "bio": {"zh": "簡介", "en": "Biography"},
    "entry_year": {"zh": "入學年度", "en": "Entry year"},
    "graduation_year": {"zh": "畢業年度", "en": "Graduation year"},
    "current_position": {"zh": "目前任職", "en": "Current position"},
    "research_subject": {"zh": "研究題目", "en": "Research topic"},
    #: 人物卡與人物頁的年份標記。中文把年份放前面、英文放後面，
    #: 這正是不能用位置參數的例子。
    "graduated_in": {"zh": "%(year)s 年畢業", "en": "Class of %(year)s"},
    "entered_in": {"zh": "%(year)s 年入學", "en": "Entered %(year)s"},
    #: 人物卡的研究焦點只列前兩項，其餘以總數帶過。
    "focus_and_more": {
        "zh": "等 %(n)s 項研究方向",
        "en": " and %(n)s topics in total",
    },
    "illustration_suffix": {"zh": " 示意圖", "en": " illustration"},
    "last_updated": {"zh": "最後更新", "en": "Last updated"},
    "further_browsing": {"zh": "延伸瀏覽", "en": "Continue browsing"},
    # --- 加入我們 ---
    "join_title": {"zh": "加入我們", "en": "Join Us"},
    "join_no_posting": {
        "zh": "目前沒有公開的招募說明。對本研究室的研究方向有興趣的同學，"
              "歡迎直接來信詢問，信中請簡述您的背景與想投入的主題。",
        "en": "There is no public recruitment posting at the moment. Students interested in "
              "the lab's research are welcome to email directly with a short note on their "
              "background and the topics they would like to work on.",
    },
    "contact_method": {"zh": "聯絡方式", "en": "Contact"},
    "supervisor": {"zh": "指導教授", "en": "Supervisor"},
    "unit": {"zh": "單位", "en": "Affiliation"},
    "address": {"zh": "地址", "en": "Address"},
    "open_in_map": {"zh": "在地圖上開啟", "en": "Open in map"},
    "before_you_apply": {"zh": "申請前建議先了解", "en": "Before you apply"},
    "step_check_focus": {"zh": "確認研究方向是否相符", "en": "Check the research fit"},
    "step_read_outputs": {"zh": "閱讀已發表的研究成果", "en": "Read the published work"},
    "step_see_team": {"zh": "看看目前的團隊組成", "en": "See the current team"},
    "step_email": {"zh": "來信聯絡", "en": "Get in touch"},
    #: 三個步驟的說明。
    #:
    #: 每一句都寫成「完整句子 + 後面獨立的連結」，而不是把
    #: <a> 夾在句子中間。夾在中間的話，一句話得拆成前綴與後綴
    #: 兩個 key（「先閱讀」+「頁面的研究方向…」），中英文語序不同
    #: 會讓其中一種語言的切點卡在很奇怪的地方，而且翻譯的人
    #: 看不到完整句子。連結改放句尾也讓三個步驟的視覺一致。
    "step_check_focus_body": {
        "zh": "讀過研究方向，確認與自己的興趣一致。",
        "en": "Read through the research focus and check it matches your own interests.",
    },
    "step_read_outputs_body": {
        "zh": "歷年期刊與會議論文都列在這裡，可從題目與發表處了解研究室的主題範圍。",
        "en": "Journal and conference papers are listed by year; the titles and venues "
              "show the range of topics the lab works on.",
    },
    "step_see_team_body": {
        "zh": "列出指導教授與目前的在學成員。",
        "en": "Lists the principal investigator and the current graduate students.",
    },
    "step_email_body": {
        "zh": "請於信中說明研究興趣、相關背景與希望投入的方向，寄至：",
        "en": "Email a short note on your research interests, background and the "
              "direction you would like to work on:",
    },
    # --- 首頁其餘區塊 ---
    "lab_facts": {"zh": "研究室事實", "en": "Lab Facts"},
    "source_paren": {"zh": "（來源）", "en": "(source)"},
    "browse_by_year": {"zh": "依年度瀏覽 →", "en": "Browse by year →"},
    "no_members_published": {"zh": "尚未發布成員資料", "en": "No member records published yet"},
    "about_the_lab": {"zh": "關於研究室", "en": "About the lab"},
    "hero_media_alt_suffix": {"zh": "研究主視覺", "en": "research key visual"},
    "photo_suffix": {"zh": "照片", "en": "photo"},
    # --- 成果詳細其餘 ---
    "related_links": {"zh": "相關連結", "en": "Related links"},
    "publisher_project_page": {"zh": "出版社／專案頁面", "en": "Publisher / project page"},
    "code_repository": {"zh": "程式碼 Repository", "en": "Code repository"},
    "dataset": {"zh": "資料集", "en": "Dataset"},
    # --- 語言回退提示 ---
    "translation_unavailable": {
        "zh": "此欄位尚無英文版本，顯示中文原文。",
        "en": "No English version available for this field; showing the Chinese original.",
    },
}


def get_lang() -> str:
    """本次請求使用的語言代碼。"""
    return getattr(g, "lang", DEFAULT_LANG)


def normalize_lang(value: str | None) -> str | None:
    """把外部輸入正規化為支援的語言代碼；不支援時回 None。

    不接受未知值是刻意的：?lang=<任意字串> 若被接受並寫入 session，
    等於讓外部輸入決定後續所有頁面的渲染分支。
    """
    if not value:
        return None
    value = value.strip().lower()
    if value in SUPPORTED_LANGS:
        return value
    # 接受 zh-TW / zh-Hant / en-US 這類常見寫法。
    if value.startswith("zh"):
        return "zh"
    if value.startswith("en"):
        return "en"
    return None


def html_lang(lang: str | None = None) -> str:
    return HTML_LANG.get(lang or get_lang(), HTML_LANG[DEFAULT_LANG])


def t(key: str, **kwargs) -> str:
    """取得介面字串。

    找不到 key 時回傳 key 本身而不是拋錯 —— 缺字串應該在測試被抓到
    （test_no_missing_translations），而不是讓正式頁面 500。
    """
    entry = STRINGS.get(key)
    if entry is None:
        return key

    # 以 is None 判斷有無翻譯，不能用真假值。
    #
    # 空字串是合法的翻譯：INTENTIONALLY_EMPTY_EN 裡的 key 就是要在
    # 英文輸出「什麼都不要」。若寫成 `entry.get(lang) or entry.get('zh')`，
    # 空字串會被當成「沒填」而回退中文 ——
    # /research 的成果筆數因此變成 "共 1 outputs"（中文前綴配英文後綴）。
    text = entry.get(get_lang())
    if text is None:
        text = entry.get(DEFAULT_LANG, key)
    if kwargs:
        try:
            return text % kwargs
        except (KeyError, ValueError, TypeError):
            return text
    return text


def localized(obj, field: str) -> tuple[str | None, str]:
    """取得物件某欄位的當前語言版本。

    Args:
        obj: 具備 `<field>_zh` / `<field>_en` 屬性的 model。
        field: 欄位基底名稱，例如 "research_focus"。

    Returns:
        (文字, 該文字實際的 html lang)。

    為什麼要回傳語言代碼：
      英文版缺值時會回退中文原文。此時該段文字仍是中文，
      若頁面 <html lang="en"> 而不標記，螢幕閱讀器會以英文腔
      逐字唸中文（WCAG 3.1.2）。呼叫端必須把回傳的語言碼
      輸出成 lang 屬性。
    """
    lang = get_lang()
    zh = getattr(obj, f"{field}_zh", None)
    en = getattr(obj, f"{field}_en", None)

    if lang == "en":
        if en and str(en).strip():
            return en, HTML_LANG["en"]
        # 回退中文，並如實標記語言。
        return (zh, HTML_LANG["zh"]) if zh else (None, HTML_LANG["zh"])

    if zh and str(zh).strip():
        return zh, HTML_LANG["zh"]
    return (en, HTML_LANG["en"]) if en else (None, HTML_LANG["zh"])


def text_lang(value) -> str | None:
    """偵測一段「沒有語言版本」的文字實際是什麼語言。

    用途：skills、keywords、degree 這類欄位在 schema 上只有一份，
    沒有 _zh / _en 之分。它們在英文頁面上照樣輸出，若不標記，
    「矽光子」會被英文語音合成逐字拼讀。

    Returns:
        含中日韓字時回傳 zh 的 html lang，否則回傳 None
        （None 代表「不要加 lang 屬性」，繼承 <html lang> 即可）。

    為什麼不做真正的語言偵測：
      這裡只需要回答「要不要蓋掉 <html lang>」這個是非題。
      引入語言偵測套件為了分辨英文與德文，對本站沒有意義，
      卻多了一個相依與一種新的錯誤模式。
    """
    if not value:
        return None
    return HTML_LANG["zh"] if _CJK_RE.search(str(value)) else None


def lang_url(target_lang: str) -> str:
    """產生「切換到 target_lang」的網址，保留當前路徑與其他查詢參數。"""
    args = request.args.to_dict(flat=True)
    if target_lang == DEFAULT_LANG:
        args.pop("lang", None)          # 中文是預設，網址不帶參數
    else:
        args["lang"] = target_lang
    try:
        return url_for(request.endpoint, **{**(request.view_args or {}), **args})
    except Exception:  # noqa: BLE001 - 例如錯誤頁沒有 endpoint
        return "/?lang=" + target_lang if target_lang != DEFAULT_LANG else "/"


def init_i18n(app) -> None:
    """掛載語言判定與 template 全域函式。"""

    @app.before_request
    def _resolve_language():
        """語言優先序：網址參數 > session > 預設。

        刻意不採用 Accept-Language 自動判斷：
        本站主要讀者是中文使用者，瀏覽器語言為英文的中文使用者
        很常見，自動切換會讓他們看到覆蓋率不完整的英文版，
        反而更難閱讀。改由使用者自己按切換鍵，行為可預期。
        """
        requested = normalize_lang(request.args.get("lang"))
        if requested:
            g.lang = requested
            if session.get(_SESSION_KEY) != requested:
                session[_SESSION_KEY] = requested
        else:
            g.lang = normalize_lang(session.get(_SESSION_KEY)) or DEFAULT_LANG

    @app.context_processor
    def _inject():
        current = get_lang()
        other = "en" if current == "zh" else "zh"
        return {
            "lang": current,
            "html_lang": html_lang(current),
            "other_lang": other,
            "t": t,
            "localized": localized,
            "text_lang": text_lang,
            "lang_url": lang_url,
        }
