# ============================================================
# NTUST SiPh Lab - Shared Validators & Normalizers
#
# 上下游：
#   blueprints/admin/forms.py -> 本模組（WTForms 自訂驗證）
#   services/person_service、research_service -> normalize_* 函式
#   services/publish_validator.py -> is_valid_url / is_valid_email
#   scripts/seed_from_google_sites.py -> normalize_url
#
# 檔案路徑：
#   app/utils/validators.py
#
# 建立日期：2026-08-14
# 最後重大修改：2026-08-14
# 版本：v1.0
#
# 模組定位與責任邊界：
#   集中所有「欄位格式」的判斷與正規化。SAI §15.1/§15.2 要求
#   URL validation、email 格式檢查、DOI normalization、
#   keywords 去重 trim。若這些規則散落在各表單，同一種欄位
#   會出現不同寬鬆度，造成資料不一致。
#
#   責任邊界（不得做的事）：
#     - 不得存取資料庫（唯一性檢查在 utils/slugs.py 與 service）。
#     - 不得決定「欄位是否必填」（那是發布門檻，見 publish_validator）。
#
# 輸入 -> 處理 -> 輸出 Pipeline：
#   normalize_url:   使用者輸入 -> 補 https:// -> 驗證 scheme/host -> 字串或 None
#   normalize_email: 使用者輸入 -> trim/lower -> 格式檢查 -> 字串或 None
#   normalize_text:  使用者輸入 -> 去除零寬字元/收斂空白 -> 字串或 None
#   parse_tag_input: "a, b, b " -> ["a", "b"]
#
# 主要 Function：
#   normalize_url / is_valid_url
#   normalize_email / is_valid_email
#   normalize_text / normalize_multiline
#   parse_tag_input
#   validate_year
#
# 依賴套件：
#   標準庫 re / urllib.parse
#
# 環境變數：無。
# 資料庫使用方式：無。
#
# Error Handling / Fallback：
#   所有 normalize_* 對「空或不合法」一律回 None，不拋例外。
#   為什麼：這些函式同時被表單與 seed script 使用。
#   表單需要的是「錯誤訊息」（由 is_valid_* 提供布林判斷），
#   而 service/seed 需要的是「乾淨值或不存值」。
#   分成 is_valid_*（判斷）與 normalize_*（轉換）兩組，
#   讓兩種需求都不必寫 try/except。
#
# 特殊機制（安全性）：
#   normalize_url 只接受 http/https。刻意排除 javascript:、data:、
#   file: 等 scheme —— 這些會出現在人物的 ORCID/GitHub 欄位並
#   直接 render 成 <a href>，若不擋就是儲存型 XSS 的入口
#   （SAI §11.2 XSS）。Jinja 的 autoescape 不會擋 href 中的
#   javascript: scheme，因此這一層檢查是必要的。
#
# 已知限制與禁止事項：
#   1. email 正則刻意寬鬆（不追求完整 RFC 5322），
#      因為過嚴的正則會誤擋合法的學術信箱。真正的驗證是
#      「管理員自己確認」（SAI 附錄 C Publish Checklist）。
#   2. 禁止用本模組驗證上傳檔案（那是 media_service 的 MIME/
#      extension allowlist）。
#
# 維護契約：
#   放寬 normalize_url 的 scheme allowlist 前必須先評估 XSS 影響，
#   並更新 tests/test_validators.py::test_dangerous_scheme_rejected。
#
# 驗證方式：
#   pytest tests/test_validators.py
# ============================================================

from __future__ import annotations

import re
from urllib.parse import urlparse, urlunparse

#: 允許的 URL scheme。擴充前請閱讀檔頭「特殊機制（安全性）」。
_ALLOWED_SCHEMES = frozenset({"http", "https"})

#: 寬鬆的 email 樣式（見檔頭「已知限制 1」）。
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

#: 零寬字元與 BOM。從網頁複製貼上的內容常夾帶這些字元，
#: 會造成「看起來一樣但字串比對不相等」，直接影響
#: legacy difference_report 的 exact match 驗證（SAI §22.5）。
_INVISIBLE_CHARS = re.compile(r"[​-‏‪-‮﻿]")

#: 連續空白（不含換行）。
_HORIZONTAL_WS = re.compile(r"[ \t]+")

#: 合法的主機名樣式（可含 userinfo 與 port，由呼叫端先行剝除）。
#:
#: 為什麼需要這個檢查：
#:   normalize_url 會為沒有 scheme 的輸入自動補上 https://（方便管理員
#:   直接貼 "example.com"）。但 urlparse 不驗證 netloc 的內容，
#:   因此 "not a url" 會變成 "https://not a url" 並被判定為合法 ——
#:   netloc 非空、scheme 合法，兩項檢查都通過。
#:   結果是明顯的錯字被存進 orcid_url / scholar_url 等欄位，
#:   前台產生一個永遠點不開的連結，而發布檢查不會攔截。
#:
#: 規則：只允許主機名合法字元，且必須含至少一個點。
#: 本專案的外部連結（NTUST、ORCID、Scholar、GitHub）皆為公開網域，
#: 一定含點；要求含點可擋掉絕大多數錯字，代價是不支援
#: 無點的內網主機名 —— 對研究室官網而言是正確的取捨。
_HOSTNAME_PATTERN = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+\.?$")


def normalize_text(value: str | None) -> str | None:
    """單行文字正規化：去零寬字元、收斂空白、trim。

    回傳 None 表示「沒有值」，讓 DB 存 NULL 而非空字串。
    為什麼要分辨 NULL 與 ''：
      SEO fallback 邏輯（SAI §12.3）以 `if seo_title exists` 判斷，
      空字串在 Python 是 falsy 但在 SQL 的 IS NULL 查詢中不成立，
      兩者混用會讓「缺 meta description」的 Needs attention 統計失準。
    """
    if value is None:
        return None
    text = _INVISIBLE_CHARS.sub("", str(value))
    text = _HORIZONTAL_WS.sub(" ", text).strip()
    return text or None


def normalize_multiline(value: str | None) -> str | None:
    """多行文字正規化：保留換行，但去除零寬字元與行尾空白。

    用於 summary/method/results 等長文欄位。
    保留換行的理由：SAI §5.3 的四段式研究內容需要段落結構。
    """
    if value is None:
        return None
    text = _INVISIBLE_CHARS.sub("", str(value))
    # 統一換行符號，避免 Windows/Unix 混用造成 diff 噪音。
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [_HORIZONTAL_WS.sub(" ", line).rstrip() for line in text.split("\n")]
    # 收斂三個以上連續空行為兩個（保留段落間距，去除過度留白）。
    cleaned: list[str] = []
    blank_run = 0
    for line in lines:
        if line == "":
            blank_run += 1
            if blank_run > 1:
                continue
        else:
            blank_run = 0
        cleaned.append(line)
    result = "\n".join(cleaned).strip()
    return result or None


def is_valid_url(value: str | None) -> bool:
    """URL 是否合法且 scheme 安全。"""
    return normalize_url(value) is not None


def normalize_url(value: str | None) -> str | None:
    """正規化 URL；不合法或危險 scheme 回 None。

    行為：
      - 無 scheme 時補 https://（管理員常直接貼 "example.com"）。
      - 只接受 http/https（見檔頭安全性說明）。
      - 必須有 netloc（主機名），否則 "https://" 這種輸入會通過。
    """
    text = normalize_text(value)
    if not text:
        return None

    # 補 scheme。判斷條件用 "://" 而非 startswith，
    # 避免把 "mailto:x@y.com" 誤判為已有 scheme 而放行。
    if "://" not in text:
        if ":" in text.split("/")[0]:
            # 形如 "javascript:alert(1)" 或 "mailto:..." -> 拒絕。
            return None
        text = "https://" + text

    try:
        parsed = urlparse(text)
    except ValueError:
        return None

    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        return None
    if not parsed.netloc:
        return None

    # 主機名必須是合法形式（見 _HOSTNAME_PATTERN 的說明）。
    # 剝除 userinfo 與 port 後再比對。
    host = parsed.netloc.rsplit("@", 1)[-1]
    if host.startswith("["):
        # IPv6 literal，例如 [::1]:8000 —— 交由 urlparse 判斷即可。
        host = host.split("]", 1)[0] + "]"
    else:
        host = host.rsplit(":", 1)[0] if ":" in host else host
    if not _HOSTNAME_PATTERN.match(host):
        return None

    # 重新組裝，確保輸出是正規化形式。
    return urlunparse(
        (
            parsed.scheme.lower(),
            parsed.netloc,
            parsed.path,
            parsed.params,
            parsed.query,
            parsed.fragment,
        )
    )


def is_valid_email(value: str | None) -> bool:
    """email 格式是否合法。"""
    return normalize_email(value) is not None


def normalize_email(value: str | None) -> str | None:
    """正規化 email：trim + lower；不合法回 None。

    為什麼要 lower：
      母站 LC-012 的 yangcl@mail.ntust.edu.tw 必須與新站做
      exact match 比對（SAI §22.5）。若大小寫不一致，
      difference_report 會誤報差異。
    """
    text = normalize_text(value)
    if not text:
        return None
    text = text.lower()
    if not _EMAIL_PATTERN.match(text):
        return None
    return text


def parse_tag_input(value: str | None) -> list[str]:
    """把 tag 輸入字串解析為去重後的清單。

    接受逗號（半形/全形）與換行分隔，對應 SAI §15.1/§15.2
    的 tag input UI 與「去重/trim」要求。

    為什麼同時接受全形逗號：
      繁體中文輸入法預設輸出全形「，」。若只接受半形，
      管理員輸入「矽光子，光通訊」會被當成單一 tag，
      這是實際使用上最常見的資料品質問題。
    """
    if not value:
        return []

    text = str(value).replace("，", ",").replace("、", ",").replace("\n", ",")
    seen: set[str] = set()
    result: list[str] = []

    for raw in text.split(","):
        item = normalize_text(raw)
        if not item:
            continue
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(item)

    return result


def validate_year(value, minimum: int = 1900, maximum: int = 2200) -> int | None:
    """驗證 4 位數年份；不合法回 None。

    上下限對應 research_outputs 的 CheckConstraint，
    確保表單層與 DB 層一致 —— 若表單放行 3000 而 DB 拒絕，
    使用者會看到 500 而不是欄位錯誤訊息。
    """
    if value is None or value == "":
        return None
    try:
        year = int(value)
    except (TypeError, ValueError):
        return None
    if year < minimum or year > maximum:
        return None
    return year
