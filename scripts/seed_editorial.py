# ============================================================
# NTUST SiPh Lab - 編輯文案 Seed（非母站來源）
#
# 上下游：
#   flask seed editorial -> run_seed() -> SettingsService.update()
#       -> site_settings.hero_intro_* / about_intro_*
#   -> SEOService（meta description 來源）與 /、/about 的可見內容
#
# 檔案路徑：
#   scripts/seed_editorial.py
#
# 建立日期：2026-08-16 / 版本：v1.0
#
# 模組定位與責任邊界：
#   母站沒有、但網站需要的「編輯文案」的唯一來源。
#
#   為什麼要獨立於 seed_from_google_sites.py：
#     那支腳本是母站遷移的證據鏈（LC-001~LC-019），
#     verify_migration.py 會逐項比對它與資料庫。把非母站的文案
#     混進去，等於在證據鏈裡放入母站根本不存在的東西 ——
#     之後就再也分不清哪一句話有母站依據、哪一句沒有。
#     seed_publications.py 基於同樣理由獨立存在。
#
#   為什麼要有這支腳本（而不是請管理者在後台打字）：
#     hero_intro_zh 就是前例。它在某次開發中被直接寫進
#     instance/siph_lab.db，沒有進版控 —— 結果是：正式站沒有它、
#     重建資料庫就消失、也沒有任何地方記載它的依據是什麼。
#     文案屬於「應該可重現」的內容，和論文書目一樣。
#
#   責任邊界（不得做的事）：
#     - 不得寫入母站有對應項目的欄位（那是 seed legacy 的職責）。
#     - 不得寫入任何無法逐句指出依據的敘述（SAI §2.3）。
#       每一則文案下方都必須有 provenance 註解說明依據來源。
#     - 不得覆寫管理者在後台的修改（預設只填空值，
#       要覆寫必須明確加 --force）。
#
# SAI §2.3 合規說明：
#   §2.3 規定「母站不存在的資料，不得由 Agent 猜測填入；
#   需另有 Lab 確認來源」。本檔的兩則文案都不是猜測：
#
#     hero_intro_en   —— hero_intro_zh 的翻譯，沒有新增任何主張。
#     about_intro_zh  —— 由「教授身分」「最早發表年份」「實際發表處」
#                        三項既有且可查證的資料組成，每一項都已經
#                        顯示在本站其他頁面上。
#
#   即便如此，兩則都是「新的句子」，因此仍必須依 §2.3 記為
#   APPROVED_REWRITE 並取得核准人與日期 ——
#   見 legacy/google_sites/content_signoff.md §2.3。
#   **核准欄位不得由 Agent 自行填寫**（ADR-011；2026-08-16 的
#   交付前稽核已因此更正過一次）。
#
# 驗證方式：
#   pytest tests/test_editorial_seed.py
#   flask seed editorial --force && flask check publish
# ============================================================

from __future__ import annotations

from app.models.site_setting import SiteSetting
from app.services.settings_service import SettingsService

# ----------------------------------------------------------------------
# 文案本體
#
# 每一則都附 provenance：這句話的每個主張各自出自哪裡。
# 新增文案時必須比照辦理 —— 沒有 provenance 的句子不得寫入。
# ----------------------------------------------------------------------

#: 首頁 Hero 導言（中）。
#:
#: 這一則是「補登」而非新寫：它早就顯示在正式頁面上，
#: 但只存在於 instance/siph_lab.db，沒有進版控（正是本檔要解決的問題）。
#: 此處照抄既有值，一字未改，之後才有東西可以比對與審核。
#:
#: provenance：由六項研究方向（母站 LC-006~011）歸納出的定位句。
#:   「矽光子」「光通訊系統」「光通道效能監視」三個詞直接來自
#:   research_focus 的 title_zh；「感測」對應「光電感測技術」。
HERO_INTRO_ZH = "以矽光子與光通訊系統為主軸，研究光訊號的感測、傳輸與效能監視。"

#: 站台預設 meta description（中）。
#:
#: 同樣是補登既有值，一字未改。
#:
#: provenance：學校（母站 LC-005 教授學歷可確認）、Lab 名稱（LC-001）、
#:   教授姓名與職稱（LC-003/004）、三項研究領域（LC-006~011）。
DEFAULT_DESCRIPTION_ZH = (
    "國立臺灣科技大學 NTUST SiPh Lab（楊淳良副教授）的研究方向、研究團隊與研究成果。"
    "研究領域涵蓋矽光子技術、光通訊系統與光通道效能監視。"
)

#: 首頁 Hero 導言（英）。
#:
#: provenance：hero_intro_zh 的翻譯，逐句對應，未新增任何主張。
#:   「矽光子」→ silicon photonics、「光通訊系統」→ optical
#:   communication systems、「光通道效能監視」→ performance
#:   monitoring：術語與 site_settings.research_focus 既有的
#:   title_en 一致（Silicon Photonics Technology / Optical
#:   Communication Systems / Optical Performance Monitoring），
#:   不另創譯名。
HERO_INTRO_EN = (
    "Research focused on silicon photonics and optical communication systems, "
    "covering the sensing, transmission and performance monitoring of optical signals."
)

#: /about 導言（中）。
#:
#: 刻意「不」與 hero_intro_zh 重複，也「不」重列下方的六項研究方向 ——
#: 兩者都已經出現在同一畫面上（這正是 2026-08-16 設計審查抓到的
#: 那類重複）。本則補的是 /about 目前完全沒有的資訊：研究產出的
#: 時間縱深與發表層級。
#:
#: provenance（逐項）：
#:   「由楊淳良副教授主持」
#:       母站 LC-003（姓名）、LC-004（職稱：副教授）；
#:       people.status = faculty，且全站僅此一位教師。
#:   「自 2004 年起持續發表」
#:       research_outputs 已發布資料中最早年份為 2004（3 篇），
#:       最新為 2025，中間 2009/2011/2012/2021/2022 皆有 ——
#:       故「持續」成立。刻意不寫篇數與最新年份：
#:       那兩個數字會隨新增論文過期，而靜態文案不會跟著更新。
#:   「IEEE Photonics Technology Letters 與 Applied Optics」
#:       兩者皆為 research_outputs.venue 的實際值，各有 DOI 可查。
#:       只列兩個而非全部：這段同時是 /about 的 meta description，
#:       中文上限 150 字（SEOService._DESCRIPTION_MAX_ZH）。
#:       原本連 Journal of Optical Communications and Networking
#:       一起列，總長 151 字，剛好被截在期刊名中間變成
#:       「…Journal of Opt…」，在搜尋結果裡看起來像壞掉的資料。
#:   「等期刊與國際會議」
#:       9 篇中 8 篇期刊、1 篇國際會議（ISPACS 2021），
#:       故此並列成立；「等」表示非窮舉。
ABOUT_INTRO_ZH = (
    "本研究室由楊淳良副教授主持，自 2004 年起持續發表光通訊與矽光子相關研究，"
    "發表處包含 IEEE Photonics Technology Letters 與 Applied Optics "
    "等期刊與國際會議。"
)

#: /about 導言（英）。
#:
#: provenance：ABOUT_INTRO_ZH 的翻譯，未新增任何主張。
#:   期刊名為專有名詞，維持原文不譯。
#:
#: 句構與中文版不同（中文一句到底，英文拆成兩句）：
#:   英文的上限是 180 字（_DESCRIPTION_MAX_EN），照中文語序直譯
#:   會來到 214 字，被截在期刊名之前，反而把最有資訊量的那半句
#:   丟掉。拆句之後 170 字，完整保留。
ABOUT_INTRO_EN = (
    "Led by Associate Professor Chun-Liang Yang. Published in IEEE Photonics "
    "Technology Letters and Applied Optics since 2004, on optical communications "
    "and silicon photonics."
)

#: 欄位 -> 文案。key 必須是 SiteSetting 的實際欄位名。
EDITORIAL_COPY: dict[str, str] = {
    "hero_intro_zh": HERO_INTRO_ZH,
    "hero_intro_en": HERO_INTRO_EN,
    "about_intro_zh": ABOUT_INTRO_ZH,
    "about_intro_en": ABOUT_INTRO_EN,
    "default_description_zh": DEFAULT_DESCRIPTION_ZH,
}

#: SettingsService.update() 會讀取的所有欄位名。
#:
#: 為什麼需要這份清單：見 _full_payload()。
_SETTING_FIELDS = tuple(
    column.key
    for column in SiteSetting.__table__.columns
    if column.key not in ("id", "created_at", "updated_at")
)


def _full_payload(setting: SiteSetting, overrides: dict[str, str]) -> dict:
    """把現有設定完整讀出來，再套上要改的欄位。

    **這一步不能省。** SettingsService.update() 是給後台表單用的，
    它對每個欄位都做 data.get(field) —— 沒帶到的 key 會拿到 None，
    然後被寫回資料庫。也就是說它是「整份覆寫」而不是「局部更新」。

    第一版的 run_seed 直接傳三個 key 進去，結果一次清空了
    university_zh/en、contact_email、official_ntust_url、
    default_title_suffix、default_description_zh、hero_intro_zh
    共七個欄位。錯誤在呼叫端而不是 SettingsService ——
    後台表單本來就會送出全部欄位，那個介面對它的使用者是正確的。

    research_focus 是 JSON 欄位，property 讀出來就是 list，
    直接放回 payload 即可（SettingsService 會處理序列化）。
    """
    payload = {field: getattr(setting, field, None) for field in _SETTING_FIELDS}
    payload["research_focus"] = setting.research_focus
    payload["social_links"] = setting.social_links
    payload.update(overrides)
    return payload


def run_seed(*, force: bool = False) -> list[str]:
    """把編輯文案寫入 SiteSetting。

    Args:
        force: 連已有內容的欄位也覆寫。預設 False —— 管理者在後台
            改過的文字優先於本檔，否則每次部署都會把人家的修改蓋掉。

    Returns:
        給 CLI 顯示的摘要行。
    """
    setting = SiteSetting.get()

    overrides: dict[str, str] = {}
    skipped: list[str] = []

    for field, text in EDITORIAL_COPY.items():
        current = (getattr(setting, field, None) or "").strip()
        if current and not force:
            skipped.append(field)
            continue
        overrides[field] = text

    summary: list[str] = []
    if overrides:
        SettingsService.update(_full_payload(setting, overrides))
        for field in overrides:
            summary.append(f"已寫入 site_settings.{field}")
    if skipped:
        summary.append(
            f"略過 {len(skipped)} 個已有內容的欄位"
            f"（{', '.join(skipped)}）—— 使用 --force 可覆寫。"
        )
    if not summary:
        summary.append("沒有需要寫入的欄位。")

    return summary
