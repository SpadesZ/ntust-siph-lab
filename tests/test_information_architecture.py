# ============================================================
# NTUST SiPh Lab - Information Architecture Tests
#
# 檔案路徑：tests/test_information_architecture.py
# 建立日期：2026-08-15 / 版本：v1.0
#
# 模組定位（SAI §4 IA、§5.1、siph-lab-web-design SKILL）：
#   審查時發現同一組資訊在站內大量重複：聯絡 Email 出現在四個
#   位置、招募文案出現在三處、同一個目的地有「加入研究室」與
#   「加入我們」兩種名稱。重複沒有讓資訊更好找，反而稀釋首頁
#   重點，也讓維護者必須記得同時更新多處。
#
#   本檔把「單一主場原則」變成可執行的斷言：
#     - 每項事實只有一個主場，其他位置只放連結
#     - footer 是例外（網站慣例，使用者預期在此找到聯絡方式）
#     - 同一目的地在站內只能有一種名稱
#
# 為什麼要測「不存在」：
#   一般測試驗證「東西有出現」，但 IA 品質的關鍵常是
#   「東西沒有出現在不該出現的地方」。這類規則若不寫成測試，
#   下一次新增 section 時很容易又把聯絡資訊複製一份。
#
# 驗證方式：
#   pytest tests/test_information_architecture.py -v
# ============================================================

from __future__ import annotations

import re

import pytest


@pytest.fixture()
def seeded(app):
    """建立含 Email 與招募文案的站台設定。"""
    from app.services.settings_service import SettingsService

    with app.app_context():
        SettingsService.update({
            "lab_name_zh": "NTUST SiPh Lab",
            "contact_email": "yangcl@mail.ntust.edu.tw",
            "address_zh": "台北市大安區基隆路四段 43 號",
            "join_title_zh": "加入我們",
            "join_body_zh": "本研究室每年招收碩士班學生。",
            "research_focus": [{"title_zh": "矽光子技術"}],
        })
    return app


def _visible_mailto_count(html: str) -> int:
    return len(re.findall(r'href="mailto:', html))


# ----------------------------------------------------------------------
# 聯絡資訊的單一主場
# ----------------------------------------------------------------------
def test_homepage_shows_contact_only_in_footer(seeded, client):
    """首頁的聯絡 Email 只應出現在 footer。

    原本首頁另有一個 Join/Contact section 重複顯示 Email 與地址，
    與 /join 完全重疊。首頁的工作是「這個研究室做什麼、有誰、
    有哪些成果」，不是再放一份聯絡簿。
    """
    html = client.get("/").get_data(as_text=True)
    assert _visible_mailto_count(html) <= 1, (
        "首頁不應重複顯示聯絡 Email（footer 以外不需要）"
    )


def test_homepage_has_no_join_section(seeded, client):
    """首頁不應再有招募區塊（招募主場是 /join，入口在主導覽）。"""
    html = client.get("/").get_data(as_text=True)
    assert "本研究室每年招收碩士班學生。" not in html, (
        "招募文案不應出現在首頁；主場是 /join"
    )


def test_about_page_does_not_repeat_email_twice_in_body(seeded, client):
    """/about 的 Email 只在教授資料出現一次（footer 不計）。"""
    html = client.get("/about").get_data(as_text=True)
    assert _visible_mailto_count(html) <= 2, (
        "/about 的 Email 應只出現於教授區塊與 footer"
    )


def test_join_page_is_the_contact_home(seeded, client):
    """招募與聯絡資訊的主場必須是 /join。"""
    html = client.get("/join").get_data(as_text=True)
    assert "本研究室每年招收碩士班學生。" in html
    assert _visible_mailto_count(html) >= 1


# ----------------------------------------------------------------------
# 命名一致性
# ----------------------------------------------------------------------
@pytest.mark.parametrize("path", ["/", "/about", "/members", "/research", "/alumni", "/join"])
def test_join_destination_has_one_consistent_name(seeded, client, path):
    """同一個目的地不得有兩種名稱。

    「加入研究室」與「加入我們」指向同一頁時，使用者會以為
    是兩個不同的地方。全站統一為主導覽使用的「加入我們」。
    """
    html = client.get(path).get_data(as_text=True)
    assert "加入研究室" not in html, (
        f"{path} 使用了「加入研究室」；全站應統一為「加入我們」"
    )


def test_join_link_present_in_main_nav(seeded, client):
    """招募入口必須固定存在於主導覽（取代首頁的重複區塊）。"""
    html = client.get("/").get_data(as_text=True)
    nav = re.search(r'<nav class="site-nav".*?</nav>', html, re.S)
    assert nav, "主導覽必須存在"
    assert "加入我們" in nav.group(0)
    assert "/join" in nav.group(0)


# ----------------------------------------------------------------------
# 首頁仍須有明確下一步（SAI §5.1，不得因去重而變成死路）
# ----------------------------------------------------------------------
def test_homepage_still_links_to_every_main_section(seeded, client):
    """去除 Join section 後，首頁仍須通往所有主要頁面。

    SAI §5.1 要求「明確下一步」、§12.1 要求「不存在孤兒 published
    page」。去重不能以製造死路為代價。
    """
    html = client.get("/").get_data(as_text=True)
    for target in ("/about", "/members", "/research", "/alumni", "/join"):
        assert f'href="{target}"' in html, f"首頁應可通往 {target}"


def test_homepage_hero_cta_points_to_content(seeded, client):
    """Hero 的 CTA 指向內容而非招募。

    訪客第一次到站時想知道的是「這裡做什麼研究」，
    招募是後續才會關心的事。
    """
    html = client.get("/").get_data(as_text=True)
    hero = re.search(r'<section class="hero".*?</section>', html, re.S)
    assert hero, "hero section 必須存在"
    assert "/research" in hero.group(0)
    assert "/join" not in hero.group(0), "Hero CTA 不應是招募（主導覽已有入口）"


# ----------------------------------------------------------------------
# 校徽（使用者需求）
# ----------------------------------------------------------------------
def test_logo_absent_by_default_without_placeholder(seeded, client):
    """未上傳校徽時不得顯示破圖或佔位圖。"""
    html = client.get("/").get_data(as_text=True)
    assert 'class="site-brand__logo"' not in html


def test_logo_appears_in_header_when_uploaded(app, client):
    """上傳校徽後應顯示於頁首與 favicon。"""
    import io

    from PIL import Image
    from werkzeug.datastructures import FileStorage

    from app.services.settings_service import SettingsService

    buffer = io.BytesIO()
    Image.new("RGB", (256, 256), (16, 35, 61)).save(buffer, format="PNG")
    buffer.seek(0)

    with app.app_context():
        SettingsService.update_media(
            "logo_path",
            FileStorage(stream=buffer, filename="ntust-logo.png", content_type="image/png"),
        )

    html = client.get("/").get_data(as_text=True)
    assert 'class="site-brand__logo"' in html
    assert 'rel="icon"' in html


# ----------------------------------------------------------------------
# 教授簡介（使用者需求）
# ----------------------------------------------------------------------
def test_professor_bio_renders_when_provided(app, client):
    """教授簡介填寫後應顯示於 /about。"""
    from app.models.mixins import PersonStatus
    from app.services.person_service import PersonService

    bio = "楊淳良副教授長期investigate矽光子被動與主動元件於光通訊系統的應用。"

    with app.app_context():
        person = PersonService.create({
            "name_zh": "楊淳良",
            "name_en": "Chun-Liang Yang",
            "status": PersonStatus.FACULTY,
            "title_zh": "副教授",
            "research_focus_zh": "矽光子技術",
            "bio_zh": bio,
        })
        PersonService.publish(person)

    assert bio in client.get("/about").get_data(as_text=True)


def test_about_has_no_empty_bio_block_when_absent(app, client):
    """未填簡介時不得產生空白區塊。

    SAI §2.3：母站沒有教授的敘述性介紹，不得由系統代筆，
    因此「留空」是正常狀態，版面必須能優雅處理。
    """
    from app.models.mixins import PersonStatus
    from app.services.person_service import PersonService

    with app.app_context():
        person = PersonService.create({
            "name_zh": "楊淳良",
            "status": PersonStatus.FACULTY,
            "title_zh": "副教授",
            "research_focus_zh": "矽光子技術",
        })
        PersonService.publish(person)

    html = client.get("/about").get_data(as_text=True)
    assert 'class="prose prose--preserve"></div>' not in html
    assert client.get("/about").status_code == 200
