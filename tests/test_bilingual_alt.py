# ============================================================
# NTUST SiPh Lab - 圖片替代文字的雙語行為
#
# 上下游：
#   PersonForm.photo_alt_en / ResearchForm.hero_image_alt_en
#       -> i18n.localized(obj, 'photo_alt' / 'hero_image_alt')
#       -> public templates（_person_card / about / person_detail /
#          research_detail）
#
# 檔案路徑：
#   tests/test_bilingual_alt.py
#
# 建立日期：2026-09-02
# 版本：v1.0
#
# 模組定位與責任邊界：
#   公開模板原本一律讀 photo_alt_zh / hero_image_alt_zh，
#   因此英文版的圖片替代文字始終是中文，而後台的
#   *_alt_en 欄位填了永遠不會被輸出。
#
#   改用 localized() 之後要守住三件事：
#     1. 英文版有填英文 -> 輸出英文
#     2. 英文版沒填英文 -> 回退中文，且必須標記 lang="zh-Hant-TW"
#        （否則螢幕閱讀器會用英文腔逐字唸中文，WCAG 3.1.2）
#     3. 中文版不受影響
#
# 主要 Function：
#   test_english_alt_is_used_on_english_page
#   test_english_page_falls_back_to_chinese_alt_with_lang_marker
#   test_chinese_page_uses_chinese_alt
#   test_research_hero_alt_is_bilingual
#
# 依賴套件：pytest, beautifulsoup4
#
# 已知限制與禁止事項：
#   1. 禁止把 alt 改回直接讀 *_alt_zh —— 那會讓 *_alt_en
#      再次變成填了沒作用的欄位。
#
# 驗證方式：
#   pytest tests/test_bilingual_alt.py
# ============================================================

from __future__ import annotations

import io

import pytest

ZH_ALT = "楊淳良副教授在光學實驗室的照片"
EN_ALT = "Portrait of Associate Professor Chun-Liang Yang in the optics lab"


def _img_alt(html: str, selector: str = "main img"):
    from bs4 import BeautifulSoup

    img = BeautifulSoup(html, "html.parser").select_one(selector)
    return (img.get("alt"), img.get("lang")) if img else (None, None)


@pytest.fixture()
def person_with_alts(app, png_bytes):
    """建立一位「中英 alt 都填了」的已發布成員。"""
    from werkzeug.datastructures import FileStorage

    from app.services.person_service import PersonService

    with app.app_context():
        person = PersonService.create(
            {
                "name_zh": "楊淳良",
                "name_en": "Chun-Liang Yang",
                "status": "faculty",
                "research_focus_zh": "矽光子與光通訊",
                "research_focus_en": "Silicon photonics and optical communication",
            }
        )
        PersonService.attach_photo(
            person,
            FileStorage(stream=io.BytesIO(png_bytes), filename="p.png", content_type="image/png"),
            alt_zh=ZH_ALT,
        )
        PersonService.update(
            person,
            {
                "name_zh": "楊淳良",
                "name_en": "Chun-Liang Yang",
                "status": "faculty",
                "research_focus_zh": "矽光子與光通訊",
                "photo_alt_zh": ZH_ALT,
                "photo_alt_en": EN_ALT,
            },
        )
        PersonService.publish(person)
        return {"slug": person.slug}


def test_english_alt_is_used_on_english_page(client, person_with_alts):
    """英文版有填英文 alt 時要輸出英文。"""
    html = client.get(f"/people/{person_with_alts['slug']}?lang=en").get_data(as_text=True)
    alt, lang = _img_alt(html)

    assert alt == EN_ALT, "英文版應輸出英文替代文字"
    assert lang == "en", "英文內容應標記 lang=en"


def test_chinese_page_uses_chinese_alt(client, person_with_alts):
    """中文版維持中文 alt（不得被英文蓋掉）。"""
    html = client.get(f"/people/{person_with_alts['slug']}").get_data(as_text=True)
    alt, lang = _img_alt(html)

    assert alt == ZH_ALT
    assert lang == "zh-Hant-TW"


def test_english_page_falls_back_to_chinese_alt_with_lang_marker(app, client, png_bytes):
    """英文 alt 留空時回退中文，且必須標記為中文。

    這是 WCAG 3.1.2：頁面是 <html lang="en"> 但這段文字其實是中文，
    不標記的話螢幕閱讀器會用英文腔逐字唸中文。
    """
    from werkzeug.datastructures import FileStorage

    from app.services.person_service import PersonService

    with app.app_context():
        person = PersonService.create(
            {
                "name_zh": "只有中文替代文字",
                "status": "current",
                "research_focus_zh": "測試研究方向",
            }
        )
        PersonService.attach_photo(
            person,
            FileStorage(stream=io.BytesIO(png_bytes), filename="p.png", content_type="image/png"),
            alt_zh=ZH_ALT,
        )
        PersonService.publish(person)
        slug = person.slug

    html = client.get(f"/people/{slug}?lang=en").get_data(as_text=True)
    alt, lang = _img_alt(html)

    assert alt == ZH_ALT, "沒有英文版時應回退中文，而不是變成空的"
    assert lang == "zh-Hant-TW", "回退的中文必須標記真實語言（WCAG 3.1.2）"


def test_research_hero_alt_is_bilingual(app, client, sample_output, png_bytes):
    """成果主圖的 alt 與圖說同樣要雙語。"""
    from werkzeug.datastructures import FileStorage

    from app.extensions import db
    from app.models.research_output import ResearchOutput
    from app.services.research_service import ResearchService

    zh, en = "量測結果圖", "Measurement result chart"

    with app.app_context():
        output = db.session.get(ResearchOutput, sample_output["id"])
        ResearchService.attach_hero_image(
            output,
            FileStorage(stream=io.BytesIO(png_bytes), filename="h.png", content_type="image/png"),
            alt_zh=zh,
        )
        ResearchService.update(
            output,
            {
                "output_type": output.output_type,
                "year": output.year,
                "title_zh": output.title_zh,
                "hero_image_alt_zh": zh,
                "hero_image_alt_en": en,
            },
        )
        slug = output.slug

    # 語言會記在 session（?lang= 之後會沿用），因此兩次請求都明確帶參數，
    # 否則測試結果會取決於請求順序而非實際行為。
    en_html = client.get(f"/research/{slug}?lang=en").get_data(as_text=True)
    zh_html = client.get(f"/research/{slug}?lang=zh").get_data(as_text=True)

    assert _img_alt(en_html, ".output-hero-image")[0] == en
    assert _img_alt(zh_html, ".output-hero-image")[0] == zh
    assert en in en_html, "圖說也應輸出英文"
