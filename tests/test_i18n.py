# ============================================================
# NTUST SiPh Lab - 雙語（zh-Hant / en）測試
#
# 上下游：
#   app/i18n.py（STRINGS / normalize_lang / localized / lang_url）
#   app/templates/public/base.html（語言切換控制項與 hreflang）
#   app/services/seo_service.py（PageMeta.bilingual 決定 hreflang）
#
# 檔案路徑：
#   tests/test_i18n.py
#
# 建立日期：2026-08-16 / 版本：v1.0
#
# 模組定位與責任邊界：
#   驗證「一鍵中英切換」這件事，包含四個層次：
#     1. 字串層：STRINGS 兩種語言都齊全。
#     2. 請求層：語言判定、session 記憶、拒絕未知輸入。
#     3. 版面層：每個公開頁都有切換控制項，且切換後真的換語言。
#     4. 誠實層：缺英文時如實回退並標記 lang；
#        內容不齊全的頁面不得輸出 hreflang（SAI §4.2）。
#
#   責任邊界（不得做的事）：
#     - 不驗證翻譯品質（那是人工審閱的事）。
#     - 不驗證「英文覆蓋率要達到多少」—— 覆蓋率會隨管理者
#       填寫而變動，寫死門檻只會變成假性失敗。這裡驗證的是
#       「不管覆蓋率多少，行為都必須誠實」。
#
# 為什麼第 4 類測試是這裡最重要的：
#   前三類壞掉會被立刻看見（頁面變空、按鈕沒反應）。
#   第 4 類壞掉不會有任何視覺徵兆 —— 頁面看起來好好的，
#   但螢幕閱讀器以英文腔逐字唸中文（WCAG 3.1.2），
#   或搜尋引擎收錄了一堆其實是中文的「英文頁」。
#   這種缺陷只有機器驗得出來。
#
# 已知限制：
#   test_chinese_fallback_is_language_tagged 只檢查公開頁的
#   靜態渲染結果。管理者若在 *_en 欄位「填中文」，本測試無法
#   分辨那是回退還是內容本身 —— 那屬於內容審閱範圍。
#
# 驗證方式：
#   pytest tests/test_i18n.py -q
# ============================================================

from __future__ import annotations

import re

import pytest

from app.i18n import (
    DEFAULT_LANG,
    HTML_LANG,
    INTENTIONALLY_EMPTY_EN,
    STRINGS,
    SUPPORTED_LANGS,
    localized,
    normalize_lang,
    text_lang,
)

#: 全部公開頁（不含需要參數的詳細頁，那些另外處理）。
PUBLIC_PATHS = ("/", "/about", "/members", "/research", "/alumni", "/join")

#: CJK 統一表意文字。用來判斷一段文字「是中文」。
#: 只取主要區段即可 —— 目的是抓回退未標記，不是做語言鑑識。
CJK_RE = re.compile(r"[一-鿿]")


def _soup(client, path):
    from bs4 import BeautifulSoup

    response = client.get(path)
    assert response.status_code == 200, f"{path} 回應 {response.status_code}"
    return BeautifulSoup(response.get_data(as_text=True), "html.parser")


@pytest.fixture()
def bilingual_output(app):
    """標題與摘要中英俱全的成果 —— 唯一有資格宣告 hreflang 的頁面。

    不共用 conftest 的 sample_output：那一筆刻意只有 summary_zh，
    正好用來驗證「不齊全就不輸出 hreflang」。
    """
    from app.models.mixins import OutputType
    from app.services.research_service import ResearchService

    with app.app_context():
        output = ResearchService.create(
            {
                "output_type": OutputType.JOURNAL,
                "year": 2025,
                "title_zh": "以類神經網路即時監視高速訊號品質",
                "title_en": "Real-Time Monitoring of High-Speed Signal Quality",
                "summary_zh": (
                    "本研究以非同步取樣與振幅排序取得訊號統計特徵，"
                    "再以類神經網路推估通道品質指標，並完成 FPGA 實作驗證。"
                ),
                "summary_en": (
                    "This work extracts statistical features via asynchronous sampling "
                    "and amplitude sorting, then estimates channel quality metrics with "
                    "a neural network, verified by an FPGA implementation."
                ),
                "venue": "International Journal of Circuit Theory and Applications",
                "doi": "10.1002/cta.2025.99999",
            }
        )
        ResearchService.publish(output)
        return output.slug


# ------------------------------------------------------------------
# 1. 字串層
# ------------------------------------------------------------------


def test_no_missing_translations():
    """每個介面字串都必須兩種語言齊全。

    這是 i18n.py 的維護契約：新增 key 時漏填一種語言，
    頁面不會報錯而是靜靜地顯示另一種語言，人眼很難察覺。
    """
    missing = {}
    for key, entry in STRINGS.items():
        blank = [lang for lang in SUPPORTED_LANGS if not (entry.get(lang) or "").strip()]
        # 中文量詞（位、共）在英文沒有對應詞，這些 key 登記在
        # INTENTIONALLY_EMPTY_EN，空英文是設計而不是遺漏。
        if key in INTENTIONALLY_EMPTY_EN:
            blank = [lang for lang in blank if lang != "en"]
        if blank:
            missing[key] = blank

    assert not missing, f"以下介面字串缺少翻譯：{missing}"


def test_intentionally_empty_keys_all_exist():
    """豁免名單不得列出已經不存在的 key。

    否則刪掉字串之後，名單會默默地繼續豁免一個不存在的東西，
    等到有人新增同名 key 就會意外被放行。
    """
    unknown = sorted(INTENTIONALLY_EMPTY_EN - set(STRINGS))
    assert not unknown, f"豁免名單有不存在的 key：{unknown}"


def test_placeholders_match_across_languages():
    """同一個 key 的兩種語言必須用同一組具名佔位符。

    少一個 %(lab)s 只會讓那個語言少顯示一段文字，不會報錯；
    多一個不存在的佔位符則會讓 t() 靜靜地回傳未格式化的原字串。
    兩種都是人眼很難發現的缺陷。
    """
    placeholder = re.compile(r"%\((\w+)\)")
    mismatched = {}
    for key, entry in STRINGS.items():
        names = {lang: set(placeholder.findall(entry[lang])) for lang in SUPPORTED_LANGS}
        if len(set(map(frozenset, names.values()))) > 1:
            mismatched[key] = names

    assert not mismatched, f"佔位符不一致：{mismatched}"


@pytest.mark.parametrize(
    "value,expected_zh",
    [
        ("矽光子", True),
        ("矽光子, FPGA", True),
        ("FPGA", False),
        ("Python", False),
        ("", False),
        (None, False),
    ],
)
def test_text_lang_detects_chinese(value, expected_zh):
    """沒有語言分版的欄位（skills/keywords）要靠這個決定要不要標 lang。"""
    result = text_lang(value)
    assert (result == HTML_LANG["zh"]) is expected_zh
    if not expected_zh:
        assert result is None, "非中文應回傳 None，代表繼承 <html lang>"


def test_language_names_are_written_in_their_own_language():
    """語言名稱不隨介面語言翻譯。

    看不懂當前語言的使用者，就是要靠這個標籤找到自己看得懂的
    那一個。若在英文介面把「中文」翻成 "Chinese"，中文使用者
    在英文頁面上就找不到回去的路。
    """
    for lang in SUPPORTED_LANGS:
        assert STRINGS["lang_name_zh"][lang] == "中文"
        assert STRINGS["lang_name_en"][lang] == "English"


# ------------------------------------------------------------------
# 2. 請求層
# ------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("zh", "zh"),
        ("en", "en"),
        ("EN", "en"),
        ("  en  ", "en"),
        ("zh-TW", "zh"),
        ("zh-Hant", "zh"),
        ("en-US", "en"),
        ("", None),
        (None, None),
        ("de", None),
        ("../../etc/passwd", None),
        ("<script>", None),
    ],
)
def test_normalize_lang(raw, expected):
    assert normalize_lang(raw) == expected


def test_default_language_is_zh(client):
    soup = _soup(client, "/")
    assert soup.html["lang"] == HTML_LANG["zh"]


def test_lang_query_switches_interface(client):
    """?lang=en 換掉介面字串與 <html lang>。"""
    zh = _soup(client, "/")
    en = _soup(client, "/?lang=en")

    assert en.html["lang"] == HTML_LANG["en"]

    zh_nav = [a.get_text(strip=True) for a in zh.select(".site-nav__link")]
    en_nav = [a.get_text(strip=True) for a in en.select(".site-nav__link")]
    assert zh_nav == ["首頁", "關於", "研究成員", "研究成果", "畢業生", "加入我們"]
    assert en_nav == ["Home", "About", "Members", "Research", "Alumni", "Join Us"]


def test_language_choice_persists_across_requests(client):
    """切換一次之後，後續不帶參數的頁面仍維持該語言。

    「一鍵切換」的意思是按一次就好，不是每個連結都要自己帶參數。
    """
    client.get("/?lang=en")
    assert _soup(client, "/members").html["lang"] == HTML_LANG["en"]
    assert _soup(client, "/research").html["lang"] == HTML_LANG["en"]

    client.get("/?lang=zh")
    assert _soup(client, "/members").html["lang"] == HTML_LANG["zh"]


def test_unknown_lang_does_not_change_language(client):
    """未知語言代碼一律忽略，且不得寫進 session。

    若 ?lang=<任意字串> 會被記住，等於讓外部輸入決定後續
    所有頁面的渲染分支。
    """
    assert _soup(client, "/?lang=klingon").html["lang"] == HTML_LANG[DEFAULT_LANG]
    # 後續請求仍是預設語言，代表垃圾值沒有被寫入 session。
    assert _soup(client, "/").html["lang"] == HTML_LANG[DEFAULT_LANG]


def test_unknown_lang_does_not_clobber_existing_choice(client):
    """已選英文後，收到垃圾 lang 值不得把使用者踢回中文。"""
    client.get("/?lang=en")
    assert _soup(client, "/?lang=klingon").html["lang"] == HTML_LANG["en"]


# ------------------------------------------------------------------
# 3. 版面層：切換控制項
# ------------------------------------------------------------------


@pytest.mark.parametrize("path", PUBLIC_PATHS)
def test_language_switch_present_on_every_public_page(client, path):
    """每一頁都要能切換，而且控制項的結構一致。"""
    switch = _soup(client, path).select_one(".lang-switch")
    assert switch is not None, f"{path} 沒有語言切換控制項"

    items = switch.select(".lang-switch__item")
    assert len(items) == len(SUPPORTED_LANGS)

    current = switch.select(".lang-switch__item--current")
    assert len(current) == 1, "必須剛好標示一個當前語言"
    assert current[0].get("aria-current") == "true"
    assert current[0].name == "span", "當前語言不可點，就不該是連結"

    links = switch.select("a.lang-switch__item")
    assert len(links) == len(SUPPORTED_LANGS) - 1
    assert links[0].get("hreflang"), "切換連結必須宣告目標語言"


@pytest.mark.parametrize("path", PUBLIC_PATHS)
def test_language_switch_stays_on_the_same_page(client, path):
    """切換語言不得把使用者丟回首頁。

    這是語言切換最常見的實作缺陷：讀者看到第 3 頁的成果列表，
    按下 English 卻回到首頁，得重新找一次。
    """
    switch = _soup(client, path).select_one(".lang-switch")
    href = switch.select_one("a.lang-switch__item")["href"]
    assert href.split("?")[0] == path


def test_language_switch_preserves_query_parameters(client):
    """/research 的篩選條件在切換語言後必須保留。"""
    soup = _soup(client, "/research?year=2021&type=conference")
    href = soup.select_one("a.lang-switch__item")["href"]

    assert "lang=en" in href
    assert "year=2021" in href
    assert "type=conference" in href


def test_switching_back_to_chinese_drops_the_parameter(client):
    """中文是預設語言，網址不該留下 ?lang=zh 這種贅字。"""
    soup = _soup(client, "/?lang=en")
    href = soup.select_one("a.lang-switch__item")["href"]

    assert "lang" not in href
    assert href in ("/", "/?")


@pytest.mark.parametrize(
    "path,zh_fragment,en_fragment",
    [
        ("/about", "關於研究室", "About the lab"),
        ("/members", "研究成員", "Research Members"),
        ("/research", "研究成果", "Research Outputs"),
        ("/alumni", "畢業生", "Alumni"),
        ("/join", "加入我們", "Join Us"),
    ],
)
def test_page_title_follows_the_language(client, path, zh_fragment, en_fragment):
    """<title> 也要換語言 —— 它是英文讀者在分頁上唯一看得到的字。

    中文版維持「中文 English」並列（給搜尋結果用，canonical 指向中文版）；
    英文版不留中文前綴，否則窄窄的瀏覽器分頁會被看不懂的字佔掉。
    """
    zh_title = _soup(client, path).title.string
    en_title = _soup(client, f"{path}?lang=en").title.string

    assert zh_fragment in zh_title
    assert en_fragment in en_title
    assert not CJK_RE.search(en_title.split("|")[0]), (
        f"英文版 <title> 的主體仍含中文：{en_title!r}"
    )


def test_language_switch_is_outside_the_main_nav(client):
    """語言切換不是導覽目的地，不得放在 <nav aria-label="主導覽"> 內。

    放進去的話，螢幕閱讀器列出主導覽時會多念一個項目，
    但它不會把使用者帶到任何新頁面。
    """
    soup = _soup(client, "/")
    nav = soup.select_one("nav.site-nav")
    assert nav.select_one(".lang-switch") is None
    assert soup.select_one("header .lang-switch") is not None


def test_language_switch_needs_no_javascript(client):
    """切換必須是純連結（SAI §18：功能不依賴 JS）。"""
    soup = _soup(client, "/")
    switch = soup.select_one(".lang-switch")

    assert switch.select_one("script") is None
    assert switch.select_one("button") is None
    for element in switch.select("*"):
        assert not any(
            attribute.startswith("on") for attribute in element.attrs
        ), "不得使用 inline event handler"


# ------------------------------------------------------------------
# 4. 誠實層：回退標記與 hreflang 門檻
# ------------------------------------------------------------------


class _Fake:
    """最小的 model 替身。localized() 只用 getattr，不需要真的 ORM 物件。"""

    def __init__(self, **fields):
        for key, value in fields.items():
            setattr(self, key, value)


def test_localized_prefers_current_language(app):
    obj = _Fake(name_zh="楊淳良", name_en="Chun-Liang Yang")

    with app.test_request_context("/?lang=en"):
        app.preprocess_request()
        text, tag = localized(obj, "name")

    assert text == "Chun-Liang Yang"
    assert tag == HTML_LANG["en"]


def test_localized_falls_back_and_reports_the_real_language(app):
    """缺英文時回退中文，且回傳的語言碼必須是 zh 而不是 en。

    回傳語言碼是整個回退機制的重點：呼叫端要把它輸出成
    lang 屬性，螢幕閱讀器才知道這段要用中文唸。
    """
    obj = _Fake(research_focus_zh="矽光子元件設計", research_focus_en=None)

    with app.test_request_context("/?lang=en"):
        app.preprocess_request()
        text, tag = localized(obj, "research_focus")

    assert text == "矽光子元件設計"
    assert tag == HTML_LANG["zh"], "回退的中文不得被標記成英文"


def test_localized_ignores_whitespace_only_english(app):
    """只有空白的 *_en 等同沒填，必須回退中文而不是印出空白。"""
    obj = _Fake(bio_zh="研究矽光子元件。", bio_en="   ")

    with app.test_request_context("/?lang=en"):
        app.preprocess_request()
        text, tag = localized(obj, "bio")

    assert text == "研究矽光子元件。"
    assert tag == HTML_LANG["zh"]


@pytest.mark.parametrize("path", PUBLIC_PATHS)
def test_no_fake_hreflang_on_incomplete_pages(client, path):
    """內容不齊全的頁面不得輸出 hreflang（SAI §4.2）。

    people 與 site_settings 的 *_en 目前多數未填，這些頁面的
    「英文版」實際上大半是中文。對搜尋引擎宣告 alternate，
    等於宣稱存在一個其實不存在的英文頁面。
    """
    soup = _soup(client, path)
    assert soup.select('link[rel="alternate"][hreflang]') == []


def test_hreflang_present_when_page_is_genuinely_bilingual(client, bilingual_output):
    """成果頁的標題與摘要中英俱全，這種頁面才可以宣告 alternate。"""
    soup = _soup(client, f"/research/{bilingual_output}")
    tags = {
        link["hreflang"]: link["href"]
        for link in soup.select('link[rel="alternate"][hreflang]')
    }

    assert set(tags) == {"zh-Hant", "en", "x-default"}
    assert tags["en"].endswith("?lang=en")
    assert tags["zh-Hant"] == tags["x-default"], "x-default 應指向中文版"


def test_hreflang_absent_when_english_summary_missing(client, app):
    """同一個成果頁，抽掉 summary_en 之後就不得再宣告 alternate。

    直接驗證門檻本身會動，而不是只驗證「有資料時會輸出」——
    後者在門檻寫死成 True 時也會通過。
    """
    from app.models.mixins import OutputType
    from app.services.research_service import ResearchService

    with app.app_context():
        output = ResearchService.create(
            {
                "output_type": OutputType.JOURNAL,
                "year": 2024,
                "title_zh": "只有中文摘要的成果",
                "title_en": "Output With No English Summary",
                "summary_zh": (
                    "本研究以矽光子平台驗證光通道效能監視方法，"
                    "在不中斷傳輸的前提下取得通道品質指標。"
                ),
                "venue": "Test Venue",
            }
        )
        ResearchService.publish(output)
        slug = output.slug

    soup = _soup(client, f"/research/{slug}")
    assert soup.select('link[rel="alternate"][hreflang]') == []


@pytest.fixture()
def populated(app, sample_person, sample_output):
    """讓列表頁真的有東西可以渲染。

    沒有這個 fixture，/members 與 /research 在英文版是空的，
    語言標記測試就只驗到頁首頁尾 —— 人物卡與成果卡那些
    最容易漏標的地方一個都測不到（第一版就是這樣漏掉的）。
    另外建立一位畢業生，以覆蓋 /alumni 的年度分組卡片。
    """
    from app.models.mixins import PersonStatus
    from app.services.person_service import PersonService

    with app.app_context():
        alumnus = PersonService.create(
            {
                "name_zh": "測試畢業生",
                "status": PersonStatus.ALUMNI,
                "graduation_year": 2024,
                "degree": "碩士",
                "thesis_title_zh": "矽光子微環諧振器之光通道效能監視",
                "research_focus_zh": "光通道效能監視、矽光子元件",
                "skills": "矽光子, FPGA",
            }
        )
        PersonService.publish(alumnus)
    return {"person": sample_person, "output": sample_output}


@pytest.mark.parametrize(
    "path",
    [*PUBLIC_PATHS, "/people/{person_slug}", "/research/{output_slug}"],
)
def test_chinese_fallback_is_language_tagged(client, populated, path):
    """英文頁上的中文，必須被標記為中文（WCAG 3.1.2）。

    英文覆蓋率不完整是可以接受的（SAI §2.3 禁止自動生成英文）；
    不可接受的是「假裝那段是英文」—— 螢幕閱讀器會用英文發音
    規則逐字唸中文，結果無法辨識。

    做法：走訪所有文字節點，任何含中日韓字的節點，往上找最近
    帶 lang 屬性的祖先，該屬性必須是中文。
    """
    path = path.format(
        person_slug=populated["person"]["slug"],
        output_slug=populated["output"]["slug"],
    )
    soup = _soup(client, f"{path}?lang=en" if "?" not in path else f"{path}&lang=en")

    untagged = []
    for node in soup.find_all(string=CJK_RE):
        if node.parent.name in ("script", "style", "title"):
            continue
        text = node.strip()
        if not text:
            continue

        # 往上找最近一個帶 lang 的祖先（含自己的 parent）。
        effective = None
        for ancestor in [node.parent, *node.parents]:
            if ancestor.name is None:
                continue
            if ancestor.get("lang"):
                effective = ancestor["lang"]
                break

        if not (effective or "").lower().startswith("zh"):
            untagged.append((text[:40], effective))

    assert not untagged, (
        f"{path} 的英文版有未標記語言的中文（螢幕閱讀器會以英文腔唸）：\n  "
        + "\n  ".join(f"{text!r} -> lang={tag!r}" for text, tag in untagged)
    )
