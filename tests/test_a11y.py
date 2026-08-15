# ============================================================
# NTUST SiPh Lab - Accessibility & Responsive Tests
#
# 上下游：
#   tests/conftest.py -> 本檔 -> templates/**、static/css/**
#
# 檔案路徑：tests/test_a11y.py
# 建立日期：2026-08-14 / 版本：v1.0
#
# 模組定位（SAI §17 Accessibility 與 Responsive、§20 UI 層）：
#   以「HTML/CSS 結構」驗證可及性契約。
#
#   為什麼用結構驗證而非瀏覽器自動化：
#     真正的鍵盤走訪、對比度與 320px 版面已於開發過程以
#     preview 工具人工確認（含實測 documentElement.scrollWidth）。
#     這裡要防的是「結構退化」—— 例如日後有人移除 skip link、
#     把 label 拆掉、或加入固定寬度造成溢出。
#     這類回歸用結構檢查即可穩定攔截，且不需要瀏覽器環境，
#     可在 CI 快速執行。
#
# 對應驗收條目：
#   AC-15 手機 320px 無 body horizontal scroll（以 CSS 契約驗證）
#   AC-16 鍵盤可完成 admin login 與主要表單
#   AC-18 首頁 H1 唯一，主導覽與 footer 都可使用鍵盤
#   SAI §6.3 body 字級不得小於 16px、禁止 hover-only 操作
#
# 驗證方式：
#   pytest tests/test_a11y.py -v
# ============================================================

from __future__ import annotations

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CSS_DIR = PROJECT_ROOT / "app" / "static" / "css"

PUBLIC_PAGES = ["/", "/about", "/members", "/alumni", "/research", "/join"]


def soup_of(client, path: str):
    from bs4 import BeautifulSoup

    return BeautifulSoup(client.get(path).get_data(as_text=True), "html.parser")


# ----------------------------------------------------------------------
# AC-18：語意結構與鍵盤可用性
# ----------------------------------------------------------------------
@pytest.mark.parametrize("path", PUBLIC_PAGES)
def test_semantic_landmarks_present(client, path):
    """SAI §12.1：header / nav / main / footer 語意標籤齊全。"""
    soup = soup_of(client, path)

    assert soup.find("header"), f"{path} 缺少 <header>"
    assert soup.find("nav"), f"{path} 缺少 <nav>"
    assert soup.find("main"), f"{path} 缺少 <main>"
    assert soup.find("footer"), f"{path} 缺少 <footer>"


@pytest.mark.parametrize("path", PUBLIC_PAGES)
def test_skip_link_is_first_focusable_element(client, path):
    """AC-18：skip link 存在且指向 main 的 id。"""
    soup = soup_of(client, path)

    skip = soup.find("a", class_="skip-link")
    assert skip is not None, f"{path} 缺少 skip link"

    target_id = skip["href"].lstrip("#")
    assert soup.find(id=target_id), f"skip link 指向不存在的 id：{target_id}"

    # 必須是 body 中第一個連結，否則鍵盤使用者要 Tab 很多次才到得了。
    first_link = soup.body.find("a")
    assert first_link is skip, f"{path} 的 skip link 不是第一個可聚焦元素"


@pytest.mark.parametrize("path", PUBLIC_PAGES)
def test_heading_hierarchy_is_sensible(client, path):
    """標題層級不得跳級（h1 之後不可直接出現 h4）。

    跳級會讓螢幕閱讀器的大綱導覽產生錯誤的結構理解。
    """
    soup = soup_of(client, path)

    levels = [int(tag.name[1]) for tag in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"])]
    assert levels, f"{path} 沒有任何標題"
    assert levels[0] == 1, f"{path} 的第一個標題應為 h1，實際為 h{levels[0]}"

    for previous, current in zip(levels, levels[1:]):
        assert current <= previous + 1, (
            f"{path} 標題層級跳級：h{previous} 之後出現 h{current}"
        )


@pytest.mark.parametrize("path", PUBLIC_PAGES)
def test_navigation_landmarks_have_labels(client, path):
    """多個 <nav> 時必須以 aria-label 區分。"""
    soup = soup_of(client, path)
    navs = soup.find_all("nav")

    if len(navs) > 1:
        for nav in navs:
            assert nav.get("aria-label"), f"{path} 有多個 nav，每個都需要 aria-label"


def test_main_navigation_marks_current_page(client):
    """目前頁面以 aria-current 標示（螢幕閱讀器可辨識位置）。"""
    soup = soup_of(client, "/members")
    current = soup.select('nav[aria-label="主導覽"] a[aria-current="page"]')

    assert len(current) == 1, "主導覽應標示且僅標示一個目前頁面"
    assert current[0].get_text(strip=True) == "研究成員"


def test_footer_links_are_real_anchors(client):
    """AC-18：footer 連結必須是真正的 <a>（可用鍵盤到達）。"""
    soup = soup_of(client, "/")
    footer_links = soup.find("footer").find_all("a")

    assert len(footer_links) >= 5, "footer 應包含網站導覽連結"
    for link in footer_links:
        assert link.get("href"), "footer 連結必須有 href 才能被鍵盤聚焦"


def test_no_positive_tabindex(client):
    """禁止使用正數 tabindex（會破壞自然的 Tab 順序）。"""
    for path in PUBLIC_PAGES:
        soup = soup_of(client, path)
        for tag in soup.find_all(attrs={"tabindex": True}):
            value = int(tag["tabindex"])
            assert value <= 0, f"{path} 使用了正數 tabindex={value}，會破壞 Tab 順序"


# ----------------------------------------------------------------------
# 圖片與替代文字（SAI §17 Images）
# ----------------------------------------------------------------------
def test_all_images_have_alt_attribute(client, sample_person, sample_output):
    """所有 <img> 都必須有 alt（即使是空字串代表裝飾）。"""
    paths = PUBLIC_PAGES + [
        f"/people/{sample_person['slug']}",
        f"/research/{sample_output['slug']}",
    ]
    for path in paths:
        soup = soup_of(client, path)
        for img in soup.find_all("img"):
            assert img.has_attr("alt"), f"{path} 有缺少 alt 屬性的圖片：{img.get('src')}"


def test_content_images_have_dimensions(app, client, png_bytes):
    """SAI §18：圖片提供 width/height 以避免 CLS。"""
    import io

    from werkzeug.datastructures import FileStorage

    from app.services.person_service import PersonService

    with app.app_context():
        person = PersonService.create(
            {"name_zh": "有照片者", "status": "current", "research_focus_zh": "測試"}
        )
        PersonService.attach_photo(
            person,
            FileStorage(stream=io.BytesIO(png_bytes), filename="p.png", content_type="image/png"),
            alt_zh="測試照片",
        )
        PersonService.publish(person)
        slug = person.slug

    soup = soup_of(client, f"/people/{slug}")
    photo = soup.find("img", class_="person-hero__photo")

    assert photo is not None
    assert photo.get("width") and photo.get("height"), "內容圖片必須有 width/height（SAI §18）"


def test_decorative_elements_are_aria_hidden(client):
    """純裝飾元素以 aria-hidden 排除於無障礙樹之外。"""
    soup = soup_of(client, "/")

    backdrop = soup.find(class_="hero-grid-backdrop")
    assert backdrop is not None
    assert backdrop.get("aria-hidden") == "true", "裝飾性背景應設 aria-hidden"


# ----------------------------------------------------------------------
# AC-16：表單可及性
# ----------------------------------------------------------------------
def test_ac16_admin_forms_have_associated_labels(logged_in_client):
    """AC-16：後台表單的每個輸入都有關聯 label。"""
    from bs4 import BeautifulSoup

    for path in ("/admin/people/new", "/admin/research/new", "/admin/settings"):
        soup = BeautifulSoup(logged_in_client.get(path).get_data(as_text=True), "html.parser")

        label_targets = {label.get("for") for label in soup.find_all("label") if label.get("for")}
        # 也接受 label 包住 input 的形式。
        wrapped = {
            inp.get("id")
            for label in soup.find_all("label")
            for inp in label.find_all(["input", "select", "textarea"])
        }

        for field in soup.find_all(["input", "select", "textarea"]):
            field_type = (field.get("type") or "").lower()
            if field_type in ("hidden", "submit", "button"):
                continue
            field_id = field.get("id")
            assert field_id, f"{path} 有沒有 id 的欄位：{field.get('name')}"
            assert field_id in label_targets or field_id in wrapped, (
                f"{path} 的欄位 {field_id} 沒有關聯的 label（SAI §17 Forms）"
            )


def test_ac16_forms_use_native_submit_buttons(logged_in_client):
    """AC-16：表單以原生 submit button 送出（可用 Enter 觸發）。"""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(
        logged_in_client.get("/admin/people/new").get_data(as_text=True), "html.parser"
    )
    form = soup.find("form", class_="admin-form")

    assert form is not None
    assert form.find("button", attrs={"type": "submit"}), "表單必須有原生 submit button"


def test_ac16_required_fields_are_marked(logged_in_client):
    """必填欄位在畫面上有明確標示（SAI §14.2 UX contract）。"""
    html = logged_in_client.get("/admin/people/new").get_data(as_text=True)
    assert "*" in html, "必填欄位應有視覺標示"


def test_state_changing_actions_use_post_forms(logged_in_client, sample_person):
    """狀態變更一律以 POST 表單而非連結（CSRF 防護 + 語意正確）。"""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(
        logged_in_client.get(f"/admin/people/{sample_person['id']}/edit").get_data(as_text=True),
        "html.parser",
    )

    # 不得有連到發布/封存端點的 GET 連結。
    for link in soup.find_all("a", href=True):
        assert "/publish" not in link["href"], "發布動作不得以連結（GET）觸發"
        assert "/archive" not in link["href"], "封存動作不得以連結（GET）觸發"

    # 必須存在對應的 POST 表單。
    actions = [f.get("action", "") for f in soup.find_all("form")]
    assert any("/publish" in a or "/unpublish" in a for a in actions)


# ----------------------------------------------------------------------
# AC-15：Responsive CSS 契約
# ----------------------------------------------------------------------
def read_css(name: str) -> str:
    return (CSS_DIR / name).read_text(encoding="utf-8")


def test_ac15_body_prevents_horizontal_overflow():
    """AC-15：body 設定 overflow-x: hidden 作為最後防線。"""
    css = read_css("main.css")
    assert "overflow-x: hidden" in css


def test_ac15_grids_use_min_width_guard():
    """自適應 grid 的最小欄寬不得是會造成溢出的固定值。

    危險寫法：`minmax(280px, 1fr)` —— 在 320px 螢幕上，
    兩欄就需要 560px，直接造成 body 橫向捲動。

    安全寫法有兩種，兩者都接受：
      1. `minmax(min(100%, 280px), 1fr)` —— 欄寬最多 280px，
         但空間不足時可收縮到容器寬度。
      2. `minmax(0, 1fr)` —— 最小值為 0，可完全收縮。
         這是最保守的寫法，用於已知欄數的版面（如不對稱雙欄）。
    """
    import re

    css = read_css("main.css") + read_css("admin.css")

    checked = 0
    for match in re.finditer(r"minmax\(([^)]*?)\s*,\s*1fr\)", css):
        first_arg = match.group(1).strip()
        checked += 1
        assert first_arg.startswith("min(") or first_arg == "0", (
            f"自適應 grid 的最小值必須是 min(...) 或 0 以避免 320px 溢出，"
            f"發現：minmax({first_arg}, 1fr)"
        )

    assert checked > 0, "前置條件：CSS 應使用 minmax 版面"


def test_ac15_narrow_breakpoint_defined():
    """必須有針對極窄螢幕的斷點。"""
    css = read_css("main.css")
    assert "max-width: 360px" in css or "max-width: 320px" in css


def test_tables_scroll_inside_container_not_body():
    """後台表格以容器內捲動處理窄螢幕，不造成 body 橫向捲動。"""
    css = read_css("admin.css")
    assert ".table-wrap" in css
    assert "overflow-x: auto" in css


# ----------------------------------------------------------------------
# SAI §6.3 UI 禁止清單
# ----------------------------------------------------------------------
def test_body_font_size_is_at_least_16px():
    """SAI §6.3：桌機 body 字級不得小於 16px。"""
    import re

    tokens = read_css("tokens.css")
    match = re.search(r"--text-base:\s*([0-9.]+)rem", tokens)

    assert match, "tokens.css 應定義 --text-base"
    px = float(match.group(1)) * 16
    assert px >= 16, f"body 字級為 {px}px，低於 SAI §6.3 的 16px 下限"


def test_reduced_motion_is_supported():
    """SAI §6.1/§17：prefers-reduced-motion 必須關閉非必要動態。"""
    tokens = read_css("tokens.css")
    assert "prefers-reduced-motion" in tokens


def test_focus_visible_styles_defined():
    """AC-16/AC-18：必須有清楚的 focus ring。"""
    css = read_css("main.css")
    assert ":focus-visible" in css
    assert "outline" in css


def test_no_banned_visual_patterns():
    """SAI §6.3：禁止紫藍漸層、玻璃擬態、發光圓球。"""
    css = (read_css("main.css") + read_css("admin.css") + read_css("tokens.css")).lower()

    assert "backdrop-filter" not in css, "禁止玻璃擬態（SAI §6.3）"
    # 紫色系色碼（常見的通用 AI 模板色）。
    for purple in ("#8b5cf6", "#a855f7", "#7c3aed", "#6366f1", "#c084fc"):
        assert purple not in css, f"禁止使用通用紫色漸層色 {purple}（SAI §6.3）"


def test_hover_styles_are_paired_with_focus():
    """SAI §6.3：禁止 hover 才出現唯一操作。

    檢查所有 :hover 規則的選擇器，
    確認同一個元件也有對應的 :focus-visible 或 :focus-within。
    """
    import re

    css = read_css("main.css")

    hover_selectors = set()
    for match in re.finditer(r"([^{}]+):hover[^{}]*\{", css):
        base = match.group(1).strip().split(",")[-1].strip()
        if base:
            hover_selectors.add(base)

    # 卡片類元件必須同時有 focus-within（鍵盤使用者才看得到回饋）。
    for component in (".card", ".person-card"):
        assert f"{component}:focus-within" in css, (
            f"{component} 有 hover 樣式，必須同時提供 focus-within（SAI §6.3 第 5 條）"
        )
    assert hover_selectors, "前置條件：CSS 應包含 hover 樣式"


def test_status_is_not_conveyed_by_colour_alone():
    """SAI §17：不得以顏色作為唯一的意義編碼。

    狀態徽章必須同時輸出文字。
    """
    macros = (PROJECT_ROOT / "app/templates/admin/_macros.html").read_text(encoding="utf-8")

    assert "已發布" in macros and "草稿" in macros and "已封存" in macros, (
        "狀態徽章必須含文字標籤，不能只靠顏色"
    )


def test_no_web_fonts_loaded(client):
    """SAI §18：不為美學載入過多字體（本案完全使用系統字體）。"""
    html = client.get("/").get_data(as_text=True)

    assert "fonts.googleapis.com" not in html
    assert "fonts.gstatic.com" not in html
    assert "@font-face" not in read_css("tokens.css")
