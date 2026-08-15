# ============================================================
# NTUST SiPh Lab - Slug Utility Tests
#
# 檔案路徑：tests/test_slugs.py
# 建立日期：2026-08-15 / 版本：v1.0
#
# 模組定位（SAI §4.2 URL 原則、§20 Unit 層）：
#   slug 一旦公開即視為永久識別（§4.2），因此產生規則的
#   任何行為改變都可能讓既有網址失效。這些測試把規則鎖住。
#
# 重點：
#   - 英文小寫、不含 URL encoding（§4.2）
#   - 中文姓名轉拼音
#   - 碰撞時的唯一化策略
#   - 空值與純符號的 fallback
#
# 驗證方式：
#   pytest tests/test_slugs.py -v
# ============================================================

from __future__ import annotations

import pytest

from app.utils.slugs import cjk_name_slug, ensure_unique_slug, slugify, suggest_person_slug


# ----------------------------------------------------------------------
# slugify
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Silicon Photonics", "silicon-photonics"),
        ("Optical  Performance   Monitoring", "optical-performance-monitoring"),
        ("Micro-Ring Resonator", "micro-ring-resonator"),
        ("  leading and trailing  ", "leading-and-trailing"),
        ("UPPER CASE", "upper-case"),
        ("with_underscores", "with-underscores"),
        ("dots.and.dots", "dots-and-dots"),
    ],
)
def test_slugify_basic(raw, expected):
    assert slugify(raw) == expected


def test_slugify_strips_punctuation():
    """標點不得留在 slug 中（§4.2：避免 URL encoding）。"""
    result = slugify("Ring Modulator: A 100 Gb/s Demo!")
    assert ":" not in result and "/" not in result and "!" not in result
    assert result.startswith("ring-modulator")


def test_slugify_no_leading_or_trailing_hyphen():
    assert not slugify("---hello---").startswith("-")
    assert not slugify("---hello---").endswith("-")


def test_slugify_respects_max_length():
    result = slugify("a" * 300, max_length=50)
    assert len(result) <= 50
    assert not result.endswith("-")


@pytest.mark.parametrize("empty", [None, "", "   ", "!!!", "---"])
def test_slugify_falls_back_when_no_usable_characters(empty):
    """空值或純符號必須產生可用的 fallback，不得回空字串。

    空 slug 會產生 /people/ 這種指向列表頁的網址，
    造成 route 衝突與難以除錯的 404。
    """
    result = slugify(empty, fallback_prefix="item")
    assert result
    assert result.startswith("item")


# ----------------------------------------------------------------------
# 中文姓名
# ----------------------------------------------------------------------
def test_cjk_name_slug_produces_ascii():
    """中文姓名必須轉為 ASCII slug（§4.2 明確要求）。"""
    result = cjk_name_slug("楊淳良")
    assert result
    assert result.isascii()
    assert " " not in result


def test_cjk_name_slug_is_deterministic():
    """同樣的輸入必須永遠得到同樣的 slug。

    若不穩定，重新 seed 母站資料會產生不同網址，
    破壞 §4.2「slug 一旦公開即視為永久識別」。
    """
    assert cjk_name_slug("陳泓序") == cjk_name_slug("陳泓序")


def test_cjk_name_slug_distinguishes_different_names():
    names = ["陳泓序", "謝卓雅", "鍾宇辰", "陳志翰"]
    slugs = [cjk_name_slug(n) for n in names]
    assert len(set(slugs)) == len(names), f"不同姓名不得產生相同 slug：{slugs}"


# ----------------------------------------------------------------------
# suggest_person_slug
# ----------------------------------------------------------------------
def test_suggest_person_slug_prefers_english_name():
    """有英文姓名時優先使用，較符合國際慣例。"""
    result = suggest_person_slug("楊淳良", "Chun-Liang Yang")
    assert result == "chun-liang-yang"


def test_suggest_person_slug_falls_back_to_chinese():
    result = suggest_person_slug("楊淳良", None)
    assert result
    assert result.isascii()


def test_suggest_person_slug_handles_all_empty():
    result = suggest_person_slug(None, None)
    assert result


# ----------------------------------------------------------------------
# ensure_unique_slug（需要 DB：它直接查詢 model）
# ----------------------------------------------------------------------
def test_ensure_unique_slug_returns_original_when_free(app):
    from app.models.person import Person

    with app.app_context():
        assert ensure_unique_slug(Person, "ring-modulator") == "ring-modulator"


def test_ensure_unique_slug_appends_suffix_on_collision(app, sample_person):
    """既有 slug 被佔用時應附加 -2（而非 -1）。"""
    from app.models.person import Person

    with app.app_context():
        result = ensure_unique_slug(Person, sample_person["slug"])
        assert result == f"{sample_person['slug']}-2"


def test_ensure_unique_slug_handles_repeated_collisions(app):
    """連續碰撞必須持續遞增，且不得無限迴圈。"""
    from app.extensions import db
    from app.models.mixins import PersonStatus
    from app.models.person import Person

    with app.app_context():
        for suffix in ("dup", "dup-2", "dup-3"):
            db.session.add(
                Person(
                    slug=suffix,
                    name_zh=f"重複測試{suffix}",
                    status=PersonStatus.CURRENT,
                    research_focus_zh="測試 slug 唯一化",
                )
            )
        db.session.commit()

        assert ensure_unique_slug(Person, "dup") == "dup-4"


def test_ensure_unique_slug_excludes_self(app, sample_person):
    """編輯既有資料時，自己的 slug 不算碰撞。

    否則每次按儲存都會把 slug 改成 xxx-2、xxx-3…，
    等於每次編輯都換一次網址，直接違反 §4.2。
    """
    from app.models.person import Person

    with app.app_context():
        result = ensure_unique_slug(
            Person, sample_person["slug"], exclude_id=sample_person["id"]
        )
        assert result == sample_person["slug"]


def test_ensure_unique_slug_respects_max_length(app):
    from app.models.person import Person

    with app.app_context():
        result = ensure_unique_slug(Person, "x" * 200, max_length=40)
        assert len(result) <= 40
