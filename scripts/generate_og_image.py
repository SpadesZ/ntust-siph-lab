# ============================================================
# NTUST SiPh Lab - 預設 Open Graph 圖片產生器
#
# 檔案路徑：scripts/generate_og_image.py
# 建立日期：2026-09-03 / 版本：v1.0
#
# 模組定位：
#   產生 app/static/img/og-default.png —— 當管理者未上傳自訂
#   OG 圖片時，全站分享預覽的回退圖（SAI §12.1 Open Graph）。
#
# 為什麼是 build-time 腳本而不是 runtime 產生：
#   這張圖的內容只有在實驗室改名時才會變。每個 request 都重畫
#   一次是純粹的浪費，而且會把 Pillow 的字體相依帶進 production
#   —— Docker 映像檔裡沒有微軟正黑體，runtime 產生只會得到
#   一整排豆腐方塊。因此在本機執行一次，把 PNG 產物 commit 進
#   repo，部署環境只是把它當一般靜態檔送出。
#
# 何時需要重跑：
#   後台修改「實驗室名稱 / 所屬學校 / 系所」或更換校徽之後。
#   圖上的文字讀自 SiteSetting，但 PNG 是產物 —— 改了設定不會
#   自動反映，必須重跑本腳本並 commit 新的 PNG。
#
#   這是刻意的取捨：省下每個 request 重畫的成本，代價是多一個
#   人工步驟。若未來設定變動頻繁到這件事會被忘記，再考慮改成
#   啟動時產生一次並快取。
#
# 設計（SAI §6.1 Photon Trace Editorial）：
#   深藍底 + 大留白 + 左對齊編輯式排版 + 少量 photon teal 焦點。
#   刻意不放漸層、發光或裝飾圖形 —— 這張圖出現在 LINE / Facebook
#   的分享卡片裡，讀者只會掃過 0.5 秒，要傳達的就是
#   「哪個學校的哪個實驗室」，多一個元素都是干擾。
#
# 尺寸 1200x630：
#   Open Graph 建議的 1.91:1。各平台裁切策略不同，因此重要內容
#   全部留在中央安全區內，四周 80px 邊距不放任何文字。
#
# 使用方式：
#   .venv/Scripts/python.exe scripts/generate_og_image.py
#
#   需要可讀取的資料庫（文字讀自 SiteSetting）與系統上的 CJK
#   字體；兩者缺一都會明確報錯，不會靜默產生壞圖。
#
# 驗證方式：
#   pytest tests/test_seo.py
#   人工：檢視 app/static/img/og-default.png
# ============================================================
from __future__ import annotations

import os
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# 讓腳本可直接以 `python scripts/generate_og_image.py` 執行
# （此時 sys.path[0] 是 scripts/，找不到 app 套件）。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# --- 版面常數 --------------------------------------------------
WIDTH, HEIGHT = 1200, 630
MARGIN = 80

# 取自 static/css/tokens.css，與網站主色一致。
COLOR_NAVY = (16, 35, 61)  # --color-navy #10233D
COLOR_SURFACE = (255, 255, 255)
COLOR_PHOTON_BRIGHT = (46, 214, 214)  # --color-photon-bright #2ED6D6
COLOR_MUTED = (169, 192, 212)  # 與 .site-brand__en 同色 #a9c0d4

# --- 內容 ------------------------------------------------------
# 三行文字全部讀自 SiteSetting，不在此寫死。
#
# 初版曾把實驗室名與所屬單位寫成常數，理由是「靜態資產不該相依
# 於資料庫」。那個判斷是錯的：所屬單位當時在後台是空的，常數裡
# 填的值沒有任何東西會去校對它，於是圖片與網站可以無聲地各說各話
# —— 實際上第一版產出的圖就漏了「先進半導體科技研究所」。
# 分享預覽是多數人對這個實驗室的第一印象，不該是站上唯一一份
# 沒有單一真實來源的文字。
#
# 代價是腳本需要 app context；這與 backup_sqlite / verify_migration
# 的做法一致，不是本檔獨有的負擔。
ROOT = Path(__file__).resolve().parent.parent
EMBLEM = ROOT / "app" / "static" / "img" / "ntust-emblem-source.webp"
OUTPUT = ROOT / "app" / "static" / "img" / "og-default.png"

# 微軟正黑體。Latin 與繁中共用同一份，字重靠不同檔案而非合成粗體
# —— Pillow 沒有 faux bold，用 Regular 假裝粗體只會得到細字。
FONT_CANDIDATES_BOLD = [
    "C:/Windows/Fonts/msjhbd.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
]
FONT_CANDIDATES_REGULAR = [
    "C:/Windows/Fonts/msjh.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
]


def _load_font(candidates: list[str], size: int) -> ImageFont.FreeTypeFont:
    """載入第一個存在的字體檔。

    找不到就直接失敗，不回退到 Pillow 的內建點陣字型 ——
    那個字型沒有 CJK 字符，會把「國立臺灣科技大學」畫成一排
    豆腐方塊，而且不會拋任何錯。靜默產生壞圖比明確失敗更糟：
    壞圖會一路 commit 進 repo，直到有人分享連結才發現。
    """
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    sys.exit(
        f"找不到可用的 CJK 字體，已嘗試：\n  "
        + "\n  ".join(candidates)
        + "\n請安裝其中一種，或修改 FONT_CANDIDATES_* 指向系統上的中文字體。"
    )


def _load_content() -> tuple[str, str, str]:
    """從 SiteSetting 取得要畫在圖上的三行文字。

    一律取中文欄位：這張圖是單一檔案、服務所有語言的分享預覽，
    而主要受眾（招生對象）是台灣的學生。英文讀者會看到中文圖，
    但 og:title / og:description 仍是英文，卡片整體讀得通。
    真要分語言就得產兩張圖並讓 SEOService 依 lang 挑選，
    為目前的英文流量做這件事並不划算。
    """
    from app import create_app  # 延遲 import：載入模組時不需要 Flask
    from app.models.site_setting import SiteSetting

    app = create_app(os.environ.get("APP_ENV", "local"))
    with app.app_context():
        site = SiteSetting.query.first()
        if site is None:
            sys.exit("SiteSetting 尚未初始化，請先執行 seed 或啟動一次 app。")
        return (
            site.lab_name_zh or "",
            site.university_zh or "",
            site.department_zh or "",
        )


def main() -> None:
    lab_name, university, department = _load_content()
    if not lab_name:
        sys.exit("SiteSetting.lab_name_zh 是空的，無法產生圖片。")

    canvas = Image.new("RGB", (WIDTH, HEIGHT), COLOR_NAVY)
    draw = ImageDraw.Draw(canvas)

    # --- 校徽 ---
    # 圓形細筆畫校徽縮到 132px 仍可辨識；再小會糊成一個藍點
    # （與 main.css 的 .site-brand__logo 40px 下限同一個判斷）。
    if EMBLEM.exists():
        emblem = Image.open(EMBLEM).convert("RGBA")
        emblem.thumbnail((132, 132), Image.LANCZOS)
        # 第三個參數傳 emblem 自己當 mask，保留 alpha 去背；
        # 少了它，透明處會被填成黑色方塊。
        canvas.paste(emblem, (MARGIN, MARGIN), emblem)
    else:
        print(f"警告：找不到校徽 {EMBLEM}，改以純文字版面產生。", file=sys.stderr)

    font_title = _load_font(FONT_CANDIDATES_BOLD, 78)
    font_uni = _load_font(FONT_CANDIDATES_REGULAR, 34)
    font_dept = _load_font(FONT_CANDIDATES_REGULAR, 30)

    # --- 主標 ---
    # 基線放在垂直中央偏下，讓校徽與文字之間留出大片空白 ——
    # 分享卡片在時間軸上很小，留白是唯一能讓標題被看見的手段。
    title_y = 288
    draw.text((MARGIN, title_y), lab_name, font=font_title, fill=COLOR_SURFACE)

    # --- photon teal 短線 ---
    # 呼應站上 .trace-underline 的視覺語彙，寬度刻意只有 96px：
    # 它是標點符號，不是分隔線。
    rule_y = title_y + 108
    draw.rectangle([MARGIN, rule_y, MARGIN + 96, rule_y + 5], fill=COLOR_PHOTON_BRIGHT)

    # --- 機構識別：大學 -> 系所，由大到小 ---
    # 兩行都留在 630px 高度內的安全區；department 缺值時不留空行，
    # 否則圖片下半部會出現一塊沒有理由的空白。
    draw.text((MARGIN, rule_y + 40), university, font=font_uni, fill=COLOR_SURFACE)
    if department:
        draw.text((MARGIN, rule_y + 92), department, font=font_dept, fill=COLOR_MUTED)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    # optimize=True：這張圖每次被分享都要重新抓，值得多花一點
    # 產生時間換傳輸量。
    canvas.save(OUTPUT, "PNG", optimize=True)

    size_kb = OUTPUT.stat().st_size / 1024
    print(f"已產生 {OUTPUT.relative_to(ROOT)} ({WIDTH}x{HEIGHT}, {size_kb:.0f} KB)")


if __name__ == "__main__":
    main()
