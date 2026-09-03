# ============================================================
# NTUST SiPh Lab - SEO Metadata Service
#
# 上下游：
#   blueprints/public/routes.py -> SEOService.build(...) -> PageMeta
#       -> templates/public/base.html（<title>/<meta>/<link rel=canonical>/OG）
#   SEOService -> repositories（sitemap 內容來源）
#   SEOService -> SiteSetting（default suffix / description / OG image）
#   SEOService -> config.PUBLIC_BASE_URL（canonical absolute URL）
#
# 檔案路徑：
#   app/services/seo_service.py
#
# 建立日期：2026-08-14
# 最後重大修改：2026-08-14
# 版本：v1.0
#
# 模組定位與責任邊界：
#   SAI §12 技術 SEO 的單一實作點。負責產生每頁的 title、
#   meta description、canonical absolute URL 與 Open Graph 欄位，
#   並實作 §12.3 的 fallback 規則。
#
#   為什麼要集中：
#     SAI §23.1 要求「新增 public page 必須同時處理 title、
#     description、canonical、sitemap inclusion」。若每個 route
#     各自組字串，遲早會有頁面漏掉 canonical 或產生重複 title
#     （§12.1 驗收：不重複、不空白）。集中後，新增頁面只能
#     透過 build() 取得 metadata，漏掉就沒有 metadata 可用，
#     問題會在開發當下就暴露。
#
#   責任邊界（不得做的事）：
#     - 不得產生 JSON-LD（那是 schema_service，因為 structured data
#       有額外的「必須與可見內容一致」約束 [S7]）。
#     - 不得 render HTML。
#     - 不得寫資料庫。
#
# 輸入 -> 處理 -> 輸出 Pipeline：
#   輸入：page 種類 + 實體物件（Person / ResearchOutput / None）
#   處理：override -> 實體衍生 -> 站台預設 的三段式 fallback、
#         描述截斷至 140-180 字、canonical 絕對化
#   輸出：PageMeta dataclass（供 base.html 直接展開）
#
# 主要 Class / Function：
#   PageMeta                     - 頁面 metadata 的不可變容器
#   SEOService.absolute_url(path)- 相對路徑 -> 絕對 URL
#   SEOService.build_home/about/members/alumni/research_index/join
#   SEOService.build_person(person)
#   SEOService.build_research(output)
#   SEOService.truncate_description(text)
#
# 依賴套件：
#   flask（current_app、url_for、request）、app.models
#
# 環境變數：
#   PUBLIC_BASE_URL - canonical 的根。production 必須為正式網域
#                     （SAI §24：launch 前 canonical/public base URL
#                     必須鎖定）。
#
# 資料庫使用方式：
#   只讀 SiteSetting 與傳入的實體，不寫入。
#
# Error Handling / Fallback：
#   §12.3 的三段式 fallback 保證 title/description 永不為空：
#     title:       seo_title_zh -> 實體衍生 -> 站台名稱
#     description: seo_description_zh -> summary/research_focus
#                  -> thesis -> 站台預設
#   任何一段缺值都會往下一段，最終一定有值。
#
# 特殊機制（canonical 與 query 參數）：
#   canonical 一律「不含 query string」。SAI §4.2 明定
#   「列表排序、追蹤參數不可產生新的 canonical」。
#   因此 /research?year=2026 的 canonical 是 /research。
#   這是刻意行為：篩選結果不是獨立內容單位。
#
# 已知限制與禁止事項：
#   1. 禁止在 description 塞關鍵字堆疊；SAI §13.1 要求
#      fact density 而非形容詞堆疊。
#   2. 禁止讓 canonical 指向 http（production 必須 https）。
#   3. 禁止對 draft/archived 內容產生 sitemap 條目（AC-08）。
#   4. 中文描述以「字元數」而非 byte 計算長度 —— 中文一字
#      在搜尋結果中佔的寬度約為英文兩倍，因此 140-180 字元
#      的上限對中文而言已偏長，故中文實際取較小值。
#
# 維護契約：
#   1. 新增公開頁面時，必須在此新增對應的 build_* 函式，
#      並在 sitemap_entries() 決定是否收錄。
#   2. 修改 fallback 順序必須同步更新 tests/test_seo.py，
#      因為那是 §12.3 規格的可執行版本。
#
# 驗證方式：
#   pytest tests/test_seo.py
# ============================================================

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urljoin

from flask import current_app, url_for

# 麵包屑標籤是介面字串，必須跟著語言走（見 _crumb 的說明）。
# app.i18n 只相依 flask，不碰資料庫，因此不會造成循環 import。
from app.i18n import get_lang, localized, t
from app.models.mixins import PersonStatus
from app.models.person import Person
from app.models.research_output import ResearchOutput
from app.models.site_setting import SiteSetting
from app.repositories import people as people_repo
from app.repositories import research as research_repo
from app.storage import get_storage

#: 中文 meta description 的目標長度上限。
#: SAI §12.3 給的是 140-180 字元；中文資訊密度較高，
#: 取 150 可避免在搜尋結果被截斷。
_DESCRIPTION_MAX_ZH = 150

#: 英文/混合內容的上限。
_DESCRIPTION_MAX_EN = 180

#: 內建的預設 OG 圖片（scripts/generate_og_image.py 產生）。
#:
#: 與校徽同屬「可信內建 asset」（SAI §16）：走 static/ 而非上傳
#: 路徑，因此不受 upload allowlist 限制，也不依賴 Cloud Storage
#: —— 即使 GCS 尚未設定，分享預覽仍有圖。
#:
#: 寫死 /static/ 而不用 url_for：本模組的 absolute_url 刻意不走
#: url_for(_external=True)（理由見該函式），而 app 未自訂
#: static_url_path，因此 /static 就是實際路徑。
_STATIC_OG_IMAGE = "/static/img/og-default.png"


@dataclass(frozen=True)
class PageMeta:
    """單一頁面的 SEO metadata。

    frozen=True：metadata 一旦決定就不該在 template render 途中被改，
    否則 <title> 與 og:title 可能不一致。
    """

    title: str
    description: str
    canonical: str
    #: Open Graph type：website / article / profile
    og_type: str = "website"
    og_image: str | None = None
    #: 是否允許索引。draft 預覽或 staging 用。
    noindex: bool = False
    #: 麵包屑（BreadcrumbList 與可見導覽共用），[(名稱, 絕對URL)]
    breadcrumbs: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    #: 這一頁的主要內容是否「中英雙語都齊全」。
    #:
    #: 只有為 True 時才輸出 hreflang alternate。
    #: SAI §4.2 明定「若翻譯不完整，不建立假的 hreflang 對應頁」——
    #: 對只有中文內容的頁面宣告 en 版本，等於告訴搜尋引擎存在
    #: 一個其實內容重複的英文頁。本站目前只有研究成果達到雙語齊全
    #: （title_en 9/9、summary_en 9/9），people 與 site_settings 尚未。
    bilingual: bool = False


class SEOService:
    """產生頁面 SEO metadata（SAI §12）。

    全部為 staticmethod：此服務無狀態，每次呼叫都從
    SiteSetting 與傳入實體重新計算，避免快取造成
    「改了設定但 title 沒變」的困惑。
    """

    # ------------------------------------------------------------------
    # 基礎工具
    # ------------------------------------------------------------------
    @staticmethod
    def base_url() -> str:
        """正式站台的絕對根 URL（不含尾斜線）。"""
        return (current_app.config.get("PUBLIC_BASE_URL") or "").rstrip("/")

    @staticmethod
    def absolute_url(path: str) -> str:
        """把相對路徑轉為 canonical 絕對 URL。

        為什麼不用 url_for(_external=True)：
          _external 會使用 request 的 Host header。在 Cloud Run
          背後有 Load Balancer 時，Host 可能是 run.app 內部網域，
          導致 canonical 指向非正式網址 —— 那會讓搜尋引擎索引到
          錯誤的網域（SAI §24 明列為風險）。改用設定值可確保
          canonical 永遠是我們宣告的正式網域。
        """
        base = SEOService.base_url()
        if not path:
            return base or "/"
        if path.startswith(("http://", "https://")):
            return path
        if not base:
            return path
        return urljoin(base + "/", path.lstrip("/"))

    @staticmethod
    def truncate_description(text: str | None, max_length: int | None = None) -> str:
        """把長文截斷為適合 meta description 的長度。

        行為：
          - 去除換行（meta 內容不應含換行）。
          - 在句號/逗號邊界截斷，避免切在詞中間。
          - 截斷時附加刪節號。

        為什麼在標點斷句：
          直接硬切會產生 "本研究提出一種基於矽光" 這種殘句，
          在搜尋結果中顯得像壞掉的內容，降低點閱意願。
        """
        if not text:
            return ""

        flat = " ".join(str(text).split())
        limit = max_length or (
            _DESCRIPTION_MAX_ZH
            if any("一" <= ch <= "鿿" for ch in flat)
            else _DESCRIPTION_MAX_EN
        )

        if len(flat) <= limit:
            return flat

        window = flat[:limit]
        # 在最後一個標點處斷句（保留標點本身以外的內容）。
        for punct in ("。", "；", "，", ". ", "; ", ", "):
            idx = window.rfind(punct)
            # 只有斷點夠靠後才採用，否則描述會過短。
            if idx > limit * 0.6:
                return window[:idx].rstrip("，,；; ") + "…"

        return window.rstrip() + "…"

    @staticmethod
    def _site() -> SiteSetting:
        return SiteSetting.get()

    @staticmethod
    def _text(obj, field: str) -> str | None:
        """取某欄位當前語言的值（缺值時回退另一種語言）。

        只回傳文字，不回傳語言碼 —— 呼叫端是 meta 屬性值與 title，
        兩者都沒有地方可以掛 lang。

        用於 lab_name / university / department 這類「兩種語言都填好」
        的欄位；回退只是防呆，實務上不會觸發。
        """
        return localized(obj, field)[0]

    @staticmethod
    def _desc_chain(obj, *fields: str) -> str | None:
        """依當前語言，取第一個有值的描述來源。

        與 _text() 的關鍵差別：**不跨語言回退**。

        其他地方缺英文時回退中文是對的 —— 頁面上顯示的本來就是那段
        中文，標了 lang 之後一切誠實。但 meta description 不一樣：
        它不出現在頁面上，只出現在搜尋結果與分享預覽，唯一用途是
        告訴讀者「這一頁是什麼」。塞一段讀者看不懂的語言進去，
        這個唯一用途就完全失效 —— 那不是誠實，只是沒用。

        缺值時回 None，由呼叫端接到「以事實組成的同語言描述」。
        """
        lang = get_lang()
        for name in fields:
            value = getattr(obj, f"{name}_{lang}", None)
            if value and str(value).strip():
                return str(value)
        return None

    @staticmethod
    def _person_facts(person: Person) -> str | None:
        """人物頁的事實描述：姓名、職稱、研究室、學校。

        只在該人物沒有任何當前語言的內容欄位時使用。
        對英文讀者而言「Chun-Liang Yang, Associate Professor,
        NTUST SiPh Lab」比一串看不懂的中文研究方向有用得多，
        而每一項都是資料庫裡的既有欄位，不是代寫的介紹（SAI §2.3）。

        姓名是唯一允許跨語言回退的部分：它是專有名詞。
        多數學生沒有填 name_en，但「陳泓序, NTUST SiPh Lab」仍然
        指得出這是誰的頁面，而純機構名（四位學生會拿到一模一樣的
        描述，還跟首頁撞在一起）指不出來。職稱等其他欄位不比照辦理 ——
        「碩二生」對英文讀者沒有識別作用，只是雜訊。

        連姓名都沒有時回 None —— 湊不出一句話，
        此時交給站台層級的描述比硬拼半句好。
        """
        lang = get_lang()
        name = localized(person, "name")[0]
        if not (name or "").strip():
            return None

        site = SEOService._site()
        parts: list[str] = [name.strip()]
        for value in (
            getattr(person, f"title_{lang}", None),
            SEOService._lab_name(),
            SEOService._text(site, "university"),
        ):
            value = (value or "").strip()
            if value and value not in parts:
                parts.append(value)
        return t("meta_fallback_separator").join(parts)

    @staticmethod
    def _lab_name() -> str:
        """描述句中的研究室名稱，跟著語言走。"""
        site = SEOService._site()
        return SEOService._text(site, "lab_name") or site.lab_name_zh

    @staticmethod
    def _title_with_suffix(core: str) -> str:
        """把核心標題加上站台後綴。

        SAI §12.3：
          Person title -> "{name} | {focus} | NTUST SiPh Lab"
          Research     -> "{title} | NTUST SiPh Lab"
        後綴由 SiteSetting.default_title_suffix 維護，
        未設定時使用 lab_name_zh。
        """
        site = SEOService._site()
        suffix = site.default_title_suffix or site.lab_name_zh or "NTUST SiPh Lab"
        core = (core or "").strip()

        if not core:
            return suffix
        # 避免重複後綴（例如管理者已在 override 中寫了站名）。
        if core.endswith(suffix):
            return core
        return f"{core} | {suffix}"

    @staticmethod
    def _institution_line() -> str:
        """由「研究室、學校、系所」三個事實串成的描述。

        三者都有 *_en 欄位，因此這一句在兩種語言都是完整的。
        不使用任何行銷詞彙 —— 它是事實的串接，不是文案（SAI §13.1）。
        """
        site = SEOService._site()
        parts = [
            SEOService._lab_name(),
            SEOService._text(site, "university"),
            SEOService._text(site, "department"),
        ]
        return t("meta_fallback_separator").join(p for p in parts if p)

    @staticmethod
    def _default_description() -> str:
        """站台層級的最後 fallback 描述。

        中文：default_description_zh -> hero_intro_zh -> 機構事實句。

        英文：hero_intro_en -> 機構事實句。
          刻意「不」回退到 default_description_zh。
          其他地方缺英文時回退中文是對的 —— 頁面上顯示的本來就是
          那段中文，描述說的是實話。但 meta description 不一樣：
          它的唯一用途是在搜尋結果與分享預覽裡告訴讀者「這頁是什麼」。
          對英文讀者放一段中文，這個唯一的用途就完全失效了；
          「NTUST SiPh Lab, National Taiwan University of Science and
          Technology」雖然單薄，至少答得出那個問題。

          hero_intro_en 欄位在後台已經存在（設定 → 首頁導言（英）），
          只是還沒填。填了之後這裡就會自動用它，不需要改程式。
        """
        site = SEOService._site()
        if get_lang() == "zh":
            if site.default_description_zh:
                return SEOService.truncate_description(site.default_description_zh)
            if site.hero_intro_zh:
                return SEOService.truncate_description(site.hero_intro_zh)
        elif site.hero_intro_en:
            return SEOService.truncate_description(site.hero_intro_en)

        return SEOService._institution_line()

    @staticmethod
    def _default_og_image() -> str:
        """站台預設 OG 圖片的絕對 URL。

        優先序：管理者上傳的自訂圖 -> 內建品牌圖。

        為什麼要有內建回退（原本沒有就回 None）：
          og_image_path 是選填，實務上一直是空的，於是 8 個公開頁
          有 7 頁完全不輸出 og:image —— 分享到 LINE / Facebook 只有
          一張純文字卡片。對一個要用來招生的網站，那是第一印象。
          人物頁與成果頁另有自己的圖，不受影響。

        回傳型別從 str | None 收窄為 str：現在一定有圖可用，
        呼叫端不必再處理 None。
        """
        site = SEOService._site()
        if site.og_image_path:
            try:
                return SEOService.absolute_url(
                    get_storage().public_url(site.og_image_path)
                )
            except Exception:  # noqa: BLE001 - 取不到自訂圖就用內建圖，不讓頁面失敗
                pass
        return SEOService.absolute_url(_STATIC_OG_IMAGE)

    @staticmethod
    def _image_url(object_key: str | None) -> str | None:
        """把 storage object key 轉為絕對 URL；失敗回 None。"""
        if not object_key:
            return None
        try:
            return SEOService.absolute_url(get_storage().public_url(object_key))
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _page_label(zh_label: str, en_key: str) -> str:
        """固定頁面的 <title> 核心。

        中文版維持「中文 English」並列寫法（例如「研究成員 Members」）：
        那是寫給搜尋結果看的，中英文查詢都能命中，而 canonical 永遠
        指向中文版，爬蟲拿到的一定是這一份。

        英文版只輸出英文。英文讀者的分頁標題不需要中文前綴 ——
        瀏覽器分頁很窄，前面掛著看不懂的幾個字會把真正的標題擠掉。
        """
        return t(en_key) if get_lang() == "en" else zh_label

    @staticmethod
    def _crumb(label_key: str, endpoint: str, **values) -> tuple[str, str]:
        """建立一個麵包屑節點。

        Args:
            label_key: i18n.STRINGS 的 key，不是字面文字。

        為什麼收 key 而不是文字：
          麵包屑同時是可見導覽與 BreadcrumbList JSON-LD 的來源
          （SAI §12.2 [S7] 要求兩者一致）。若在此寫死中文，
          英文頁面的麵包屑會是未標記語言的中文 —— 螢幕閱讀器
          會以英文腔唸它（WCAG 3.1.2），而 JSON-LD 也跟著只有中文。
          由 t() 統一決定，可見內容與 structured data 就一起換語言。
        """
        return (t(label_key), SEOService.absolute_url(url_for(endpoint, **values)))

    # ------------------------------------------------------------------
    # 各頁面 metadata
    # ------------------------------------------------------------------
    @staticmethod
    def build_home() -> PageMeta:
        site = SEOService._site()
        title = site.lab_name_zh
        if site.lab_name_en and site.lab_name_en != site.lab_name_zh:
            title = f"{site.lab_name_zh} {site.lab_name_en}"

        # 首頁 title 加上所屬學校作為限定詞。
        #
        # 為什麼：本站的 lab_name_zh 與 lab_name_en 都是 "NTUST SiPh Lab"，
        # 因此首頁 <title> 原本就只有這五個字。對搜尋結果而言那是
        # 一個沒有任何脈絡的標題 —— 使用者無法從中判斷這是哪個學校、
        # 哪個領域的研究室（SAI §12.1：Title 每頁唯一且有意義）。
        # 加上學校名可在不重複 lab 名稱的前提下提供最少必要脈絡。
        if site.university_zh and site.university_zh not in title:
            title = f"{title}｜{site.university_zh}"

        # 首頁不加後綴（否則會變成 "NTUST SiPh Lab | NTUST SiPh Lab"）。
        suffix = site.default_title_suffix
        if suffix and suffix not in title:
            title = f"{title} | {suffix}"

        return PageMeta(
            title=title,
            description=SEOService._default_description(),
            canonical=SEOService.absolute_url(url_for("public.home")),
            og_type="website",
            og_image=SEOService._default_og_image(),
        )

    @staticmethod
    def build_about() -> PageMeta:
        site = SEOService._site()
        # about_intro 目前只有中文欄位。英文版取不到值，會落到
        # _default_description() 的英文事實句；補上 about_intro_en
        # 欄位後這裡就會自動改用它，不需要改程式。
        description = (
            SEOService.truncate_description(SEOService._desc_chain(site, "about_intro"))
            or SEOService._default_description()
        )
        return PageMeta(
            title=SEOService._title_with_suffix(SEOService._page_label("關於研究室 About", "about_the_lab")),
            description=description,
            canonical=SEOService.absolute_url(url_for("public.about")),
            og_image=SEOService._default_og_image(),
            breadcrumbs=(SEOService._crumb("nav_home", "public.home"),),
        )

    @staticmethod
    def build_members() -> PageMeta:
        site = SEOService._site()
        count = len(people_repo.list_current_members())
        # 描述使用可驗證事實（人數），不使用形容詞（SAI §13.1）。
        lab = SEOService._lab_name()
        description = (
            t("meta_members_with_count", lab=lab, n=count)
            if count
            else t("meta_members_empty", lab=lab)
        )
        return PageMeta(
            title=SEOService._title_with_suffix(SEOService._page_label("研究成員 Members", "members_title")),
            description=description,
            canonical=SEOService.absolute_url(url_for("public.members")),
            og_image=SEOService._default_og_image(),
            breadcrumbs=(SEOService._crumb("nav_home", "public.home"),),
        )

    @staticmethod
    def build_alumni() -> PageMeta:
        site = SEOService._site()
        groups = people_repo.list_alumni_by_year()
        count = sum(len(members) for _, members in groups)
        lab = SEOService._lab_name()
        description = (
            t("meta_alumni_with_count", lab=lab, n=count)
            if count
            else t("meta_alumni_empty", lab=lab)
        )
        return PageMeta(
            title=SEOService._title_with_suffix(SEOService._page_label("畢業生 Alumni", "alumni_title")),
            description=description,
            canonical=SEOService.absolute_url(url_for("public.alumni")),
            og_image=SEOService._default_og_image(),
            breadcrumbs=(SEOService._crumb("nav_home", "public.home"),),
        )

    @staticmethod
    def build_research_index() -> PageMeta:
        site = SEOService._site()
        count = len(research_repo.published_outputs())
        lab = SEOService._lab_name()
        description = (
            t("meta_research_with_count", lab=lab, n=count)
            if count
            else t("meta_research_empty", lab=lab)
        )
        return PageMeta(
            title=SEOService._title_with_suffix(SEOService._page_label("研究成果 Research", "research_title")),
            description=description,
            # canonical 不含篩選參數（見檔頭「特殊機制」）。
            canonical=SEOService.absolute_url(url_for("public.research_index")),
            og_image=SEOService._default_og_image(),
            breadcrumbs=(SEOService._crumb("nav_home", "public.home"),),
        )

    @staticmethod
    def build_join() -> PageMeta:
        site = SEOService._site()
        # join_body 目前只有中文欄位（同 about）。沒有招募文案時走
        # 事實樣板，那一則兩種語言都有，因此英文版一定是英文。
        description = (
            SEOService.truncate_description(SEOService._desc_chain(site, "join_body"))
            or t("meta_join_default", lab=SEOService._lab_name())
        )
        return PageMeta(
            title=SEOService._title_with_suffix(
                site.join_title_zh
                if get_lang() == "zh" and site.join_title_zh
                else SEOService._page_label("加入我們 Join", "join_title")
            ),
            description=description,
            canonical=SEOService.absolute_url(url_for("public.join")),
            og_image=SEOService._default_og_image(),
            breadcrumbs=(SEOService._crumb("nav_home", "public.home"),),
        )

    @staticmethod
    def build_person(person: Person) -> PageMeta:
        """人物頁 metadata（SAI §12.3 Person 規則）。

        title fallback：
          seo_title_zh -> "{name_zh} | {research_focus_short} | {suffix}"
          -> "{name_zh} | {suffix}"
        """
        if person.seo_title_zh:
            title = SEOService._title_with_suffix(person.seo_title_zh)
        else:
            focus_short = SEOService.truncate_description(
                person.research_focus_zh or person.research_focus_en, max_length=24
            )
            core = person.name_zh
            if person.name_en:
                core = f"{person.name_zh} {person.name_en}"
            if focus_short:
                core = f"{core} | {focus_short}"
            title = SEOService._title_with_suffix(core)

        # description fallback：override -> research focus -> thesis -> bio -> 站台預設
        #
        # seo_description_zh 這個覆寫欄位只在中文版採用。
        # 它是管理者為中文搜尋結果手寫的句子，直接搬到英文頁面
        # 會讓英文版永遠是中文 —— 即使該人物的 research_focus_en
        # 明明填好了。英文版因此跳過覆寫，直接走內容鏈。
        description = (
            (person.seo_description_zh if get_lang() == "zh" else None)
            or SEOService._desc_chain(person, "research_focus", "thesis_title", "bio")
            or SEOService._person_facts(person)
        )
        description = SEOService.truncate_description(description) or SEOService._default_description()

        # 麵包屑依身分導向正確的列表頁。
        if person.status == PersonStatus.ALUMNI:
            parent = SEOService._crumb("alumni_title", "public.alumni")
        elif person.status == PersonStatus.FACULTY:
            parent = SEOService._crumb("about_the_lab", "public.about")
        else:
            parent = SEOService._crumb("members_title", "public.members")

        return PageMeta(
            title=title,
            description=description,
            canonical=SEOService.absolute_url(
                url_for("public.person_detail", slug=person.slug)
            ),
            og_type="profile",
            og_image=SEOService._image_url(person.photo_path) or SEOService._default_og_image(),
            breadcrumbs=(SEOService._crumb("nav_home", "public.home"), parent),
        )

    @staticmethod
    def build_research(output: ResearchOutput) -> PageMeta:
        """成果頁 metadata（SAI §12.3 Research 規則）。"""
        if output.seo_title_zh:
            title = SEOService._title_with_suffix(output.seo_title_zh)
        else:
            title = SEOService._title_with_suffix(output.display_title)

        # description fallback：override -> summary -> problem -> method
        #
        # 這是全站英文描述品質最高的一處：summary_en 是出版方登錄的
        # 英文摘要原文（Crossref / OpenAlex 取得，9/9 齊全），
        # 不是任何人翻譯或改寫的。英文版的成果頁描述因此是真的英文，
        # 而且可回溯到 DOI。
        #
        # seo_description_zh 覆寫同樣只在中文版採用（理由見 build_person）。
        description = (
            (output.seo_description_zh if get_lang() == "zh" else None)
            or SEOService._desc_chain(output, "summary", "problem", "method")
        )
        description = SEOService.truncate_description(description) or SEOService._default_description()

        return PageMeta(
            title=title,
            description=description,
            canonical=SEOService.absolute_url(
                url_for("public.research_detail", slug=output.slug)
            ),
            og_type="article",
            og_image=SEOService._image_url(output.hero_image_path)
            or SEOService._default_og_image(),
            breadcrumbs=(
                SEOService._crumb("nav_home", "public.home"),
                SEOService._crumb("research_title", "public.research_index"),
            ),
            # 標題與摘要兩者都具備中英文時，這一頁才真的有英文版本，
            # 才可以輸出 hreflang（SAI §4.2）。實測九篇皆滿足。
            bilingual=bool(
                (output.title_zh or "").strip()
                and (output.title_en or "").strip()
                and (output.summary_zh or "").strip()
                and (output.summary_en or "").strip()
            ),
        )

    # ------------------------------------------------------------------
    # sitemap
    # ------------------------------------------------------------------
    @staticmethod
    def sitemap_entries() -> list[dict]:
        """產生 sitemap.xml 的條目（SAI §12.1、AC-08、AC-09）。

        規則：
          - 只收 published canonical pages。
          - lastmod 使用 updated_at。
          - 靜態頁的 lastmod 取「相關內容中最新的 updated_at」，
            而非部署時間 —— 因為 /members 的內容確實隨人物更新而變。

        Returns:
            [{"loc": 絕對URL, "lastmod": ISO字串|None,
              "changefreq": str, "priority": str}, ...]
        """
        from app.utils.dates import iso_datetime

        published_people = people_repo.published_people()
        published_outputs = research_repo.published_outputs()

        def _latest(items) -> datetime | None:
            """取一組實體中最新的 updated_at。"""
            stamps = [i.updated_at for i in items if getattr(i, "updated_at", None)]
            return max(stamps) if stamps else None

        entries: list[dict] = [
            {
                "loc": SEOService.absolute_url(url_for("public.home")),
                "lastmod": iso_datetime(_latest(published_people + published_outputs)),
                "changefreq": "weekly",
                "priority": "1.0",
            },
            {
                "loc": SEOService.absolute_url(url_for("public.about")),
                "lastmod": iso_datetime(SiteSetting.get().updated_at),
                "changefreq": "monthly",
                "priority": "0.8",
            },
            {
                "loc": SEOService.absolute_url(url_for("public.members")),
                "lastmod": iso_datetime(_latest(people_repo.list_current_members())),
                "changefreq": "monthly",
                "priority": "0.8",
            },
            {
                "loc": SEOService.absolute_url(url_for("public.research_index")),
                "lastmod": iso_datetime(_latest(published_outputs)),
                "changefreq": "weekly",
                "priority": "0.9",
            },
            {
                "loc": SEOService.absolute_url(url_for("public.alumni")),
                "lastmod": iso_datetime(_latest(people_repo.list_alumni())),
                "changefreq": "monthly",
                "priority": "0.7",
            },
            {
                "loc": SEOService.absolute_url(url_for("public.join")),
                "lastmod": iso_datetime(SiteSetting.get().updated_at),
                "changefreq": "monthly",
                "priority": "0.6",
            },
        ]

        for person in published_people:
            entries.append(
                {
                    "loc": SEOService.absolute_url(
                        url_for("public.person_detail", slug=person.slug)
                    ),
                    "lastmod": iso_datetime(person.updated_at),
                    "changefreq": "monthly",
                    "priority": "0.7",
                }
            )

        for output in published_outputs:
            entries.append(
                {
                    "loc": SEOService.absolute_url(
                        url_for("public.research_detail", slug=output.slug)
                    ),
                    "lastmod": iso_datetime(output.updated_at),
                    "changefreq": "monthly",
                    "priority": "0.8",
                }
            )

        return entries
