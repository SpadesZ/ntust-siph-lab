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


@pytest.mark.parametrize("path", ["/", "/about", "/members", "/research", "/alumni"])
def test_join_link_appears_exactly_once_per_page(seeded, client, path):
    """招募入口全站只有主導覽一處。

    使用者明確要求：其他頁面都不要有，只留導覽列的「加入我們」。
    原本除了主導覽，還有 (a) 每頁 next-steps 的招募按鈕、
    (b) footer 的「網站導覽」連結列，等於每頁出現三次。
    """
    html = client.get(path).get_data(as_text=True)
    count = len(re.findall(r'href="/join"', html))
    assert count == 1, f"{path} 有 {count} 個招募連結，應只有主導覽一處"


@pytest.mark.parametrize("path", ["/about", "/members", "/research", "/alumni"])
def test_no_join_cta_button_in_page_body(seeded, client, path):
    """頁面內容區不得再放招募 CTA 按鈕。"""
    html = client.get(path).get_data(as_text=True)
    body = re.search(r"<main.*?</main>", html, re.S)
    assert body, "main 區塊必須存在"
    assert 'href="/join"' not in body.group(0), (
        f"{path} 的內容區仍有招募 CTA；入口應只在主導覽"
    )


def test_footer_does_not_duplicate_main_nav(seeded, client):
    """footer 不得逐項重複主導覽。

    主導覽在每頁頂端固定可見，本站又只有六個頁面；
    在同一畫面列兩次同一組連結不會讓人更快找到東西。
    """
    html = client.get("/").get_data(as_text=True)
    footer = re.search(r'<footer class="site-footer".*?</footer>', html, re.S)
    assert footer, "footer 必須存在"

    nav_targets = ["/about", "/members", "/research", "/alumni", "/join"]
    duplicated = [t for t in nav_targets if f'href="{t}"' in footer.group(0)]
    assert not duplicated, f"footer 重複了主導覽連結：{duplicated}"


def test_footer_still_provides_contact(seeded, client):
    """去除導覽列後，footer 仍須保留主導覽沒有的資訊（聯絡方式）。"""
    html = client.get("/").get_data(as_text=True)
    footer = re.search(r'<footer class="site-footer".*?</footer>', html, re.S).group(0)
    assert "mailto:" in footer


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
def test_builtin_ntust_emblem_shown_by_default(seeded, client):
    """未上傳自訂 logo 時顯示內建的 NTUST 校徽。

    校徽是「可信內建 asset」（SAI §16），放在 app/static/img/ 而非
    uploads/，因此不依賴 Cloud Storage —— 即使 GCS 尚未設定，
    頁首與 favicon 仍會正常顯示。
    """
    html = client.get("/").get_data(as_text=True)
    assert 'class="site-brand__logo"' in html
    assert "ntust-emblem-80.webp" in html
    assert 'alt="國立臺灣科技大學校徽"' in html
    assert "img/favicon.png" in html


def test_emblem_has_retina_source(seeded, client):
    """校徽是細筆畫圓形圖樣，必須提供 2x 以免在高解析螢幕糊掉。"""
    html = client.get("/").get_data(as_text=True)
    assert "ntust-emblem-160.webp" in html
    assert "srcset" in html


def test_emblem_alt_does_not_repeat_lab_name(seeded, client):
    """校徽 alt 不應重複緊鄰的實驗室名稱。

    圖片旁邊已經是文字形式的實驗室名稱，alt 若寫同樣內容，
    螢幕閱讀器會連續念兩次。alt 應描述「這是哪個學校」這項
    圖片才提供的資訊。
    """
    html = client.get("/").get_data(as_text=True)
    brand = re.search(r'<a class="site-brand".*?</a>', html, re.S).group(0)
    alt = re.search(r'alt="([^"]*)"', brand).group(1)
    assert "SiPh" not in alt


def test_builtin_emblem_files_exist():
    """內建校徽檔案必須實際存在（否則頁首會破圖）。"""
    from pathlib import Path

    static_img = Path(__file__).resolve().parent.parent / "app" / "static" / "img"
    for name in ("ntust-emblem-80.webp", "ntust-emblem-160.webp", "favicon.png"):
        path = static_img / name
        assert path.is_file(), f"缺少內建資產：{name}"
        assert path.stat().st_size > 0


def test_logo_appears_in_header_when_uploaded(app, client):
    """管理者上傳自訂 logo 時應覆蓋內建校徽。"""
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
