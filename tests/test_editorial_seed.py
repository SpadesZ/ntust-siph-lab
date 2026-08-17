# ============================================================
# NTUST SiPh Lab - 編輯文案 Seed 測試
#
# 上下游：
#   scripts/seed_editorial.py（EDITORIAL_COPY / run_seed）
#   app/services/settings_service.py（整份覆寫語意）
#   app/services/seo_service.py（這些文案是 meta description 的來源）
#
# 檔案路徑：
#   tests/test_editorial_seed.py
#
# 建立日期：2026-08-16 / 版本：v1.0
#
# 模組定位：
#   編輯文案與母站遷移、論文書目並列為第三種內容來源。
#   前兩者都有專屬測試（test_legacy_migration、test_publications_seed），
#   這一支補上第三者。
#
# 這裡最重要的一項是 test_seed_does_not_clear_other_settings：
#   第一版的 run_seed 只把要改的三個 key 傳給 SettingsService.update()，
#   而那個 API 是給後台表單用的「整份覆寫」——沒帶到的欄位會被寫成 None。
#   結果一次清空 university_zh/en、contact_email、official_ntust_url、
#   default_title_suffix、default_description_zh、hero_intro_zh 七個欄位。
#   資料是靠 seed legacy --force 與備份救回來的。
#   這種錯誤不會有任何例外或警告，只會讓頁面悄悄少一段文字。
#
# 驗證方式：
#   pytest tests/test_editorial_seed.py -q
# ============================================================

from __future__ import annotations

import re

import pytest

from scripts.seed_editorial import EDITORIAL_COPY, run_seed

CJK_RE = re.compile(r"[一-鿿]")

#: 與 SEOService 的上限一致。超過就會在搜尋結果被截斷。
MAX_ZH = 150
MAX_EN = 180


@pytest.fixture()
def seeded(app):
    """跑完母站 seed 的 app —— 編輯文案是疊在母站內容之上的。"""
    from scripts.seed_from_google_sites import run_seed as run_legacy_seed

    with app.app_context():
        run_legacy_seed(force=True)
    return app


def _setting(app):
    from app.models.site_setting import SiteSetting

    return SiteSetting.get()


# ------------------------------------------------------------------
# 寫入行為
# ------------------------------------------------------------------


def test_seed_writes_all_editorial_fields(seeded):
    with seeded.app_context():
        run_seed(force=True)
        setting = _setting(seeded)
        for field, expected in EDITORIAL_COPY.items():
            assert getattr(setting, field) == expected, f"{field} 未正確寫入"


def test_seed_does_not_clear_other_settings(seeded):
    """寫入文案不得清掉任何其他欄位。

    SettingsService.update() 是整份覆寫，呼叫端若只傳部分欄位，
    其餘會被寫成 None。這個測試把「seed 前的完整快照」與
    「seed 後」逐欄比對，只允許 EDITORIAL_COPY 裡的欄位改變。
    """
    from app.models.site_setting import SiteSetting

    tracked = [
        column.key
        for column in SiteSetting.__table__.columns
        if column.key not in ("id", "created_at", "updated_at")
    ]

    with seeded.app_context():
        before = {field: getattr(_setting(seeded), field) for field in tracked}
        run_seed(force=True)
        after = {field: getattr(_setting(seeded), field) for field in tracked}

    changed = {f for f in tracked if before[f] != after[f]}
    unexpected = changed - set(EDITORIAL_COPY)

    # social_links_json 由 None 正規化為 [] 屬無害的往返差異。
    unexpected.discard("social_links_json")

    assert not unexpected, (
        "seed 動到了不該動的欄位：\n  "
        + "\n  ".join(f"{f}: {before[f]!r} -> {after[f]!r}" for f in sorted(unexpected))
    )


def test_seed_preserves_admin_edits_without_force(seeded):
    """管理者在後台改過的文字優先 —— 否則每次部署都會蓋掉他的修改。"""
    from app.services.settings_service import SettingsService
    from scripts.seed_editorial import _full_payload

    custom = "管理者自己寫的關於研究室內容。"
    with seeded.app_context():
        setting = _setting(seeded)
        SettingsService.update(_full_payload(setting, {"about_intro_zh": custom}))

        run_seed(force=False)
        assert _setting(seeded).about_intro_zh == custom

        run_seed(force=True)
        assert _setting(seeded).about_intro_zh == EDITORIAL_COPY["about_intro_zh"]


def test_copy_targets_real_columns(app):
    """EDITORIAL_COPY 的 key 必須都是 SiteSetting 真的有的欄位。

    打錯欄位名不會報錯 —— SettingsService 只是忽略它，
    文案就默默地永遠不會出現在頁面上。
    """
    from app.models.site_setting import SiteSetting

    columns = {column.key for column in SiteSetting.__table__.columns}
    unknown = sorted(set(EDITORIAL_COPY) - columns)
    assert not unknown, f"EDITORIAL_COPY 有不存在的欄位：{unknown}"


# ------------------------------------------------------------------
# 文案本身的約束
# ------------------------------------------------------------------


@pytest.mark.parametrize("field", sorted(EDITORIAL_COPY))
def test_copy_fits_meta_description_limits(field):
    """這些文案同時是 meta description，超長會在搜尋結果被截斷。

    實際發生過：about_intro_zh 原本 151 字，剛好截在期刊名中間變成
    「…Journal of Opt…」，在搜尋結果裡看起來像壞掉的資料。
    """
    text = EDITORIAL_COPY[field]
    limit = MAX_ZH if CJK_RE.search(text) else MAX_EN
    assert len(text) <= limit, (
        f"{field} 長度 {len(text)} 超過 {limit} 字，會被截斷：\n  {text}"
    )


@pytest.mark.parametrize("field", sorted(f for f in EDITORIAL_COPY if f.endswith("_en")))
def test_english_copy_contains_no_chinese(field):
    """英文文案不得夾雜中文 —— 它們存在的意義就是給英文讀者看。"""
    text = EDITORIAL_COPY[field]
    assert not CJK_RE.search(text), f"{field} 含中文：{text}"


def test_about_intro_does_not_repeat_hero_intro():
    """/about 導言不得與首頁 Hero 導言重複。

    這是本專案反覆出現的那類缺陷：同一段文字在 / 與 /about 各印一次
    （2026-08-16 設計審查記錄在案）。about.html 也刻意不 fallback 到
    hero_intro_zh，理由相同。這裡守住文案來源端。
    """
    hero = EDITORIAL_COPY["hero_intro_zh"]
    about = EDITORIAL_COPY["about_intro_zh"]

    assert hero != about
    # 不只擋全等：擋「其中一句整段被另一句包含」。
    assert hero not in about and about not in hero

    # 兩者不得有過長的共同片段（連續 12 字以上視為整句照抄）。
    overlap = max(
        (
            len(hero[i:j])
            for i in range(len(hero))
            for j in range(i + 12, len(hero) + 1)
            if hero[i:j] in about
        ),
        default=0,
    )
    assert overlap == 0, f"兩則導言有 {overlap} 字的重複片段"


def test_about_intro_avoids_counts_that_go_stale():
    """導言不得寫死會過期的數字（成果篇數、最新年份）。

    靜態文案不會跟著資料庫更新。教授新增第 10 篇論文時，
    寫著「9 篇」的關於頁就開始說謊，而且沒有任何機制會提醒。
    最早年份（2004）例外：那是不會變的歷史。
    """
    about = EDITORIAL_COPY["about_intro_zh"] + EDITORIAL_COPY["about_intro_en"]

    assert "9" not in about.replace("2004", ""), "不要寫死成果篇數"
    for year in range(2020, 2031):
        if year == 2004:
            continue
        assert str(year) not in about, f"不要寫死最新年份 {year}"
