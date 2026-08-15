# ============================================================
# NTUST SiPh Lab - Input Validator Tests
#
# 檔案路徑：tests/test_validators.py
# 建立日期：2026-08-15 / 版本：v1.0
#
# 模組定位（SAI §15 欄位級規格、§20 Unit 層）：
#   這些函式是所有管理表單的第一道正規化與驗證。
#   它們的行為直接決定「什麼資料能進資料庫」，
#   因此邊界條件必須被鎖住。
#
# 重點：
#   - URL / email 格式驗證（§15.1 Links）
#   - tag input 的去重與 trim（§15.2 Keywords）
#   - 年份範圍
#   - 空白與空字串一律正規化為 None（避免 "" 與 None 混用）
#
# 驗證方式：
#   pytest tests/test_validators.py -v
# ============================================================

from __future__ import annotations

import pytest

from app.utils.validators import (
    is_valid_email,
    is_valid_url,
    normalize_email,
    normalize_multiline,
    normalize_text,
    normalize_url,
    parse_tag_input,
    validate_year,
)


# ----------------------------------------------------------------------
# 文字正規化
# ----------------------------------------------------------------------
@pytest.mark.parametrize("raw", [None, "", "   ", "\t", "\n  \n"])
def test_normalize_text_blank_becomes_none(raw):
    """空白一律轉 None。

    為什麼重要：若空字串與 None 混用，模板的 `{% if value %}`
    行為一致但資料庫的 NULL 判斷不一致，會出現
    「查詢不到但畫面顯示空白」這種難以除錯的狀況。
    """
    assert normalize_text(raw) is None


def test_normalize_text_trims_surrounding_whitespace():
    assert normalize_text("  矽光子技術  ") == "矽光子技術"


def test_normalize_text_collapses_internal_whitespace():
    """單行欄位收斂連續空白。

    這讓從網頁複製貼上的內容能與母站原文逐字比對成功
    （SAI §22.5 的 exact match 驗證）。
    """
    assert normalize_text("Silicon  Photonics") == "Silicon Photonics"


def test_normalize_text_strips_zero_width_characters():
    """零寬字元會讓「看起來一樣」的字串比對失敗。"""
    assert normalize_text("矽​光子") == "矽光子"


def test_normalize_multiline_keeps_line_breaks():
    """多行欄位（摘要、方法）必須保留換行。"""
    result = normalize_multiline("第一行\n第二行\n第三行")
    assert result is not None
    assert result.count("\n") == 2


def test_normalize_multiline_strips_outer_blank_lines():
    result = normalize_multiline("\n\n  內容  \n\n")
    assert result == "內容"


# ----------------------------------------------------------------------
# URL
# ----------------------------------------------------------------------
@pytest.mark.parametrize("url", [
    "https://innc.ntust.edu.tw/p/412-1111-12071.php?Lang=zh-tw",
    "https://orcid.org/0000-0002-1825-0097",
    "http://example.com",
    "https://github.com/user/repo",
])
def test_is_valid_url_accepts_http_urls(url):
    assert is_valid_url(url) is True


@pytest.mark.parametrize("url", [
    None, "", "   ",
    "javascript:alert(1)",      # XSS 向量
    "data:text/html,<script>",  # XSS 向量
    "ftp://example.com",        # 非 http(s)
    "//example.com",            # protocol-relative
])
def test_is_valid_url_rejects_dangerous_or_malformed(url):
    """只接受 http/https。

    javascript: 與 data: 若被寫入 href，點擊即觸發 XSS ——
    雖然 CSP 已擋下 inline script，但不應依賴單一防線。
    """
    assert is_valid_url(url) is False


@pytest.mark.parametrize("url", [
    "not a url",          # 含空白
    "https://not a url",  # 補完 scheme 後仍含空白
    "typo",               # 無點的單字（多半是錯字）
    "https://",           # 無主機名
    "https://.",          # 退化的主機名
])
def test_is_valid_url_rejects_malformed_hostnames(url):
    """主機名必須是合法形式。

    normalize_url 會為無 scheme 的輸入自動補 https://，
    但 urlparse 不驗證 netloc 內容，因此 "not a url" 曾被
    判定為合法並存進資料庫，在前台產生永遠點不開的連結。
    """
    assert is_valid_url(url) is False


def test_normalize_url_returns_none_for_blank():
    assert normalize_url("") is None
    assert normalize_url(None) is None


def test_normalize_url_trims():
    assert normalize_url("  https://example.com  ") == "https://example.com"


def test_normalize_url_adds_scheme_for_bare_domain():
    """管理員常直接貼網域，補 https:// 是刻意的便利設計。"""
    assert normalize_url("example.com") == "https://example.com"


def test_normalize_url_keeps_path_and_query():
    url = "https://innc.ntust.edu.tw/p/412-1111-12071.php?Lang=zh-tw"
    assert normalize_url(url) == url


# ----------------------------------------------------------------------
# Email
# ----------------------------------------------------------------------
@pytest.mark.parametrize("email", [
    "yangcl@mail.ntust.edu.tw",
    "a.b+tag@example.co.uk",
])
def test_is_valid_email_accepts(email):
    assert is_valid_email(email) is True


@pytest.mark.parametrize("email", [
    None, "", "   ", "no-at-sign", "@example.com", "user@", "user @example.com",
])
def test_is_valid_email_rejects(email):
    assert is_valid_email(email) is False


def test_normalize_email_lowercases():
    """Email 正規化為小寫，讓 LC-012 的逐字比對穩定。"""
    assert normalize_email("  YangCL@Mail.NTUST.edu.TW  ") == "yangcl@mail.ntust.edu.tw"


def test_normalize_email_blank_becomes_none():
    assert normalize_email("  ") is None


# ----------------------------------------------------------------------
# Tag input（§15.2 Keywords：去重 / trim）
# ----------------------------------------------------------------------
def test_parse_tag_input_splits_and_trims():
    assert parse_tag_input("矽光子, 光通訊 ,  微環諧振器") == [
        "矽光子", "光通訊", "微環諧振器"
    ]


def test_parse_tag_input_removes_duplicates():
    result = parse_tag_input("矽光子, 矽光子, 光通訊")
    assert result.count("矽光子") == 1


def test_parse_tag_input_preserves_order():
    """去重必須保留首次出現的順序（顯示順序有意義）。"""
    assert parse_tag_input("c, a, b, a") == ["c", "a", "b"]


def test_parse_tag_input_drops_empty_entries():
    assert parse_tag_input("a, , ,b,") == ["a", "b"]


@pytest.mark.parametrize("raw", [None, "", "   ", ",,,"])
def test_parse_tag_input_blank_returns_empty_list(raw):
    assert parse_tag_input(raw) == []


# ----------------------------------------------------------------------
# 年份
# ----------------------------------------------------------------------
@pytest.mark.parametrize("value,expected", [
    (2026, 2026),
    ("2026", 2026),
    ("  2026  ", 2026),
])
def test_validate_year_accepts_valid(value, expected):
    assert validate_year(value) == expected


@pytest.mark.parametrize("value", [None, "", "   ", "not-a-year", 1800, 2500, "0"])
def test_validate_year_rejects_out_of_range_or_malformed(value):
    assert validate_year(value) is None


def test_validate_year_respects_custom_bounds():
    assert validate_year(1950, minimum=2000) is None
    assert validate_year(2005, minimum=2000, maximum=2010) == 2005
