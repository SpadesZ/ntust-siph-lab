# ============================================================
# NTUST SiPh Lab - Slug Generation & Uniqueness
#
# 上下游：
#   Admin forms（自動建議 slug）-> slugify()
#   PersonService / ResearchService -> ensure_unique_slug() -> DB 查詢
#   scripts/seed_from_google_sites.py -> slugify() 產生初始 slug
#   slug -> 公開 URL /people/<slug>、/research/<slug>
#
# 檔案路徑：
#   app/utils/slugs.py
#
# 建立日期：2026-08-14
# 最後重大修改：2026-08-14
# 版本：v1.0
#
# 模組定位與責任邊界：
#   實作 SAI §4.2「URL 使用英文小寫 slug，避免姓名/標題直接做
#   URL encoding」。中文姓名必須轉為可讀的英文 slug
#   （例：陳泓序 -> hong-xu-chen），而不是 %E9%99%B3...。
#
#   責任邊界（不得做的事）：
#     - 不得在此決定「何時可以改 slug」（那是 service 層的政策）。
#     - 不得在此建立 Redirect（同上）。
#
# 輸入 -> 處理 -> 輸出 Pipeline：
#   slugify: 任意字串 -> Unicode NFKD 正規化 -> 中文轉拼音（若可用）
#            -> 非英數轉連字號 -> 收斂連續連字號 -> 小寫 -> 長度截斷
#   ensure_unique_slug: base slug -> 查 DB -> 衝突時加 -2, -3...
#
# 主要 Function：
#   slugify(text, max_length)             - 產生 slug
#   ensure_unique_slug(model, slug, ...)  - 保證 DB 唯一
#   suggest_person_slug(name_zh, name_en) - 人物 slug 建議
#
# 依賴套件：
#   標準庫 re / unicodedata；選用 pypinyin（中文轉拼音）
#
# 環境變數：無。
#
# 資料庫使用方式：
#   ensure_unique_slug 會對指定 model 的 slug 欄位做 SELECT。
#   使用 SQLAlchemy ORM，無 raw SQL（SAI §10.2 可攜契約）。
#
# Error Handling / Fallback：
#   - 無 pypinyin 時，中文字會被移除；若結果為空則以
#     fallback_prefix + 短雜湊產生（例如 "person-a1b2c3"），
#     確保永遠能得到合法且唯一的 slug，而不是空字串。
#   - 這個 fallback 是刻意的：寧可產生較不美觀的 slug，
#     也不能讓建立人物失敗或產生 /people/ 這種壞 URL。
#
# 特殊機制（唯一性競態）：
#   ensure_unique_slug 的檢查與後續 INSERT 之間存在理論上的競態。
#   本案為單一管理員（ADR-007），實際不會併發建立同名內容；
#   且 DB 層的 UNIQUE 約束是最後防線，衝突時 service 會攔截
#   IntegrityError 並重試。這是已知且已處理的限制。
#
# 已知限制與禁止事項：
#   1. slug 一經發布視為永久（SAI §4.2）；變更必須由 service
#      建立 301 Redirect，禁止直接改欄位。
#   2. 禁止產生以數字開頭且可能與路由衝突的 slug（如 "2026"）——
#      本實作允許數字開頭，但路由設計上 /research/<slug> 不會與
#      年份篩選（/research?year=2026）衝突，故安全。
#   3. 禁止在 slug 中保留底線；統一使用連字號（SEO 慣例）。
#
# 維護契約：
#   修改 slugify 規則時，既有已發布內容的 slug 不得被自動重算。
#   任何批次重算都必須同時產生 Redirect，否則會造成大量 404。
#
# 驗證方式：
#   pytest tests/test_slugs.py
# ============================================================

from __future__ import annotations

import hashlib
import re
import unicodedata

from sqlalchemy import select

from app.extensions import db

#: 非英數字元（將被轉為連字號）。
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
#: 連續連字號。
_MULTI_DASH = re.compile(r"-{2,}")

#: slug 預設最大長度。people.slug 為 VARCHAR(120)、
#: research_outputs.slug 為 VARCHAR(160)，此處取較保守值，
#: 並保留後綴（-2, -10）所需空間。
DEFAULT_MAX_LENGTH = 100

#: pypinyin 是否可用。在 module 載入時判定一次，避免每次呼叫都 try/import。
try:  # pragma: no cover - 依安裝環境而定
    from pypinyin import Style, lazy_pinyin

    _PINYIN_AVAILABLE = True
except ImportError:  # pragma: no cover
    lazy_pinyin = None  # type: ignore[assignment]
    Style = None  # type: ignore[assignment]
    _PINYIN_AVAILABLE = False


def _contains_cjk(text: str) -> bool:
    """是否含中日韓字元。"""
    return any("一" <= ch <= "鿿" for ch in text)


def _romanize(text: str) -> str:
    """把中文轉為以空白分隔的拼音；無 pypinyin 時原樣回傳。

    為什麼用 lazy_pinyin 而非 pinyin：
      lazy_pinyin 不帶聲調數字，產出的是 "chen hong xu" 而非
      "chen2 hong2 xu4"，更適合作為 URL。
    """
    if not _PINYIN_AVAILABLE or not _contains_cjk(text):
        return text
    try:
        parts = lazy_pinyin(text, style=Style.NORMAL)
        return " ".join(parts)
    except Exception:  # noqa: BLE001 - 轉換失敗時退回原文，由後續步驟處理
        return text


def slugify(text: str | None, max_length: int = DEFAULT_MAX_LENGTH, fallback_prefix: str = "item") -> str:
    """把任意文字轉為 URL-safe slug。

    Args:
        text: 來源文字（中文或英文皆可）。
        max_length: 最大長度，超過會在連字號邊界截斷。
        fallback_prefix: 無法產生有效 slug 時的前綴。

    Returns:
        非空的小寫 slug。

    為什麼一定回傳非空值：
      呼叫端（建立人物/成果）不應該需要處理「slug 產生失敗」。
      若允許回傳空字串，會產生 /people/ 這種 URL，
      直接破壞 SAI §12.1 的 canonical 與 sitemap 規則。
    """
    source = (text or "").strip()

    if source:
        # 中文先轉拼音，讓 slug 可讀（SAI §4.2 的核心目的）。
        source = _romanize(source)
        # NFKD 分解後丟棄變音符號：Café -> Cafe。
        source = unicodedata.normalize("NFKD", source)
        source = "".join(ch for ch in source if not unicodedata.combining(ch))
        source = source.lower()
        source = _NON_ALNUM.sub("-", source)
        source = _MULTI_DASH.sub("-", source).strip("-")

    if not source:
        # Fallback：以原始輸入的雜湊產生穩定且唯一的 slug。
        # 用雜湊而非隨機值，確保同一輸入重跑 seed script 會得到同一 slug。
        digest = hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:6]
        return f"{fallback_prefix}-{digest}"

    if len(source) > max_length:
        source = source[:max_length].rstrip("-")
        # 若截斷點落在單字中間，退回最後一個完整連字號邊界。
        if "-" in source:
            head, _, tail = source.rpartition("-")
            # 只有在 tail 明顯是被切斷的殘片時才丟棄（長度 < 3）。
            if head and len(tail) < 3:
                source = head

    return source or f"{fallback_prefix}"


#: 常見的兩字複姓。用於判斷中文姓名的姓氏長度。
#: 這份清單刻意只收錄常見者 —— 未收錄的複姓會被當成單字姓，
#: 產生的 slug 仍然合法可用，只是語序略有差異。
#: 管理者隨時可在後台手動指定 slug 覆寫（見 PersonForm.slug）。
_COMPOUND_SURNAMES = frozenset({
    "歐陽", "司馬", "上官", "夏侯", "諸葛", "聞人", "東方", "赫連",
    "皇甫", "尉遲", "公羊", "澹台", "公冶", "宗政", "濮陽", "淳于",
    "單于", "太叔", "申屠", "公孫", "仲孫", "軒轅", "令狐", "鍾離",
    "宇文", "長孫", "慕容", "鮮于", "閭丘", "司徒", "司空", "南宮",
})


def cjk_name_slug(name_zh: str) -> str:
    """把中文姓名轉為「名在前、姓在後」的西式語序 slug。

    為什麼要調整語序：
      SAI §4.2 明確以 `/people/hong-xu-chen` 作為範例。
      該範例對應的中文姓名是「陳泓序」（陳=姓、泓序=名），
      因此規格要求的是「given-name-first」的西式排列，
      而非直接照中文語序拼音（會得到 chen-hong-xu）。
      這與學術界英文姓名的慣用寫法一致（Hong-Xu Chen）。

    處理規則：
      1. 先判斷姓氏長度（兩字複姓優先比對，否則視為單字姓）。
      2. 名的部分先拼音，姓的部分後拼音。
      3. 姓名長度不足 2 字時直接拼音，不做拆分。

    Args:
        name_zh: 中文姓名。

    Returns:
        小寫連字號 slug，例如「陳泓序」-> "hong-xu-chen"。

    已知限制：
      1. 未收錄的複姓會被當成單字姓（見 _COMPOUND_SURNAMES 說明）。
      2. 非中文字元的姓名會退回一般 slugify 處理。
      3. 多音字由 pypinyin 的預設讀音決定，可能與本人慣用拼法不同 ——
         這正是「若本人提供英文名，優先使用英文名」的原因。
    """
    name = (name_zh or "").strip()
    if len(name) < 2 or not _contains_cjk(name):
        return slugify(name, fallback_prefix="person")

    surname_length = 2 if name[:2] in _COMPOUND_SURNAMES else 1
    surname = name[:surname_length]
    given = name[surname_length:]

    if not given:
        return slugify(name, fallback_prefix="person")

    # 名在前、姓在後（西式語序）。
    return slugify(f"{given} {surname}", fallback_prefix="person")


def suggest_person_slug(name_zh: str | None, name_en: str | None = None) -> str:
    """為人物產生建議 slug。

    優先順序：
      1. 英文名（若有）—— 那是本人認可的羅馬拼寫，
         比機器拼音更正確（例如 "Chun-Liang Yang"），
         直接對應 SAI §13.1「Consistent naming」。
      2. 中文姓名的西式語序拼音（見 cjk_name_slug）。
    """
    if name_en and name_en.strip():
        return slugify(name_en, fallback_prefix="person")
    if name_zh and _contains_cjk(name_zh):
        return cjk_name_slug(name_zh)
    return slugify(name_zh, fallback_prefix="person")


def ensure_unique_slug(
    model_class,
    desired_slug: str,
    exclude_id: int | None = None,
    max_length: int = DEFAULT_MAX_LENGTH,
) -> str:
    """回傳在該 model 中唯一的 slug。

    Args:
        model_class: 具備 id 與 slug 欄位的 model。
        desired_slug: 期望的 slug（通常來自 slugify）。
        exclude_id: 編輯既有資料時排除自己，避免誤判衝突。
        max_length: slug 欄位允許的最大長度。

    Returns:
        唯一 slug。衝突時附加 -2、-3...

    為什麼從 2 開始而非 1：
      第一筆使用裸 slug（無後綴），第二筆才是 "-2"，
      符合一般使用者對「第二個」的直覺。
    """
    base = (desired_slug or "").strip("-") or "item"
    if len(base) > max_length:
        base = base[:max_length].rstrip("-")

    candidate = base
    suffix = 2

    while True:
        stmt = select(model_class.id).where(model_class.slug == candidate)
        if exclude_id is not None:
            stmt = stmt.where(model_class.id != exclude_id)

        if db.session.scalar(stmt) is None:
            return candidate

        # 保留後綴空間後再組合，避免超過欄位長度上限。
        suffix_text = f"-{suffix}"
        trimmed = base[: max_length - len(suffix_text)].rstrip("-")
        candidate = f"{trimmed}{suffix_text}"
        suffix += 1

        # 理論上不會發生；防止異常資料造成無限迴圈。
        if suffix > 1000:  # pragma: no cover
            digest = hashlib.sha256(base.encode("utf-8")).hexdigest()[:8]
            return f"{base[: max_length - 9].rstrip('-')}-{digest}"
