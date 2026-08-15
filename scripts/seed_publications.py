# ============================================================
# NTUST SiPh Lab - Professor Publication Seed
#
# 上下游：
#   Crossref API（書目來源，2026-08-15 查證）
#       -> 本腳本的 PUBLICATIONS 常數
#       -> app/cli.py `flask seed publications`
#       -> ResearchService.create / publish
#       -> /research 與 /people/chun-liang-yang（雙向關聯）
#
# 檔案路徑：
#   scripts/seed_publications.py
#
# 建立日期：2026-08-15
# 最後重大修改：2026-08-15
# 版本：v1.0
#
# 模組定位與責任邊界：
#   母站（Google Sites）沒有任何研究成果資料，因此
#   scripts/seed_from_google_sites.py 依 SAI §2.3 明確拒絕產生 ——
#   那是正確的行為，也讓 /research 一直是空的。
#
#   本腳本是「另有 Lab 確認來源」的那條路徑（SAI §2.3 最後一句：
#   「需另有 Lab 確認來源」）。九篇論文由研究室提供清單，
#   再逐篇以 Crossref API 查證 DOI、正式標題、期刊名稱、年份與
#   作者列，全部確認含 Chun-Liang Yang。
#
#   責任邊界（不得做的事）：
#     - 不得新增未經 Lab 提供且未經 Crossref 查證的論文。
#     - 不得虛構摘要中的數值、效能或結論（見下方「摘要來源」）。
#     - 不得修改 legacy migration 的 inventory —— 這些不是母站內容，
#       與 LC-001~LC-019 是不同來源，混在一起會破壞 §22 的證據鏈。
#
# 摘要來源（重要）：
#   summary_zh 一律「只改寫標題與出處所陳述的事實」，
#   不含任何未經查證的數值、效能指標或結論。
#   例如標題寫「25-Gb/s NRZ」才會出現該數字；
#   標題沒說的結果一律不寫。
#
#   這些摘要是暫代性質，用途是讓成果頁具備可讀的入口。
#   正式的 Problem / Method / Results / Significance（SAI §5.3）
#   應由教授提供原始 abstract 後於後台補齊；
#   本腳本在執行結束時會明確提示這一點。
#
# 輸入 -> 處理 -> 輸出 Pipeline：
#   PUBLICATIONS（靜態資料）
#     -> 依 doi 判斷是否已存在（冪等）
#     -> ResearchService.create（draft）
#     -> 關聯 faculty Person
#     -> ResearchService.publish（通過發布門檻才發布）
#     -> 回傳摘要訊息清單
#
# 主要 Function：
#   run_seed(force) - 主要進入點
#
# 依賴套件：
#   app.services.research_service、app.models
#
# 環境變數：無（資料為靜態常數）。
#
# 資料庫使用方式：
#   research_outputs、research_output_people。本模組不自行 commit，
#   由 CLI 指令統一 commit（與 seed_from_google_sites 一致）。
#
# Error Handling / Fallback：
#   - 找不到 faculty Person 時中止並提示先跑 `flask seed legacy`
#     （論文必須能關聯到教授，否則失去 §13.1 的 cross-entity linking）。
#   - 個別論文發布失敗不中止整批，改記錄原因並繼續。
#
# 特殊機制（冪等性）：
#   以 DOI 作為既存判斷依據（DOI 是論文的穩定識別碼，
#   標題可能因排版而有細微差異）。重複執行不會產生重複資料。
#
# 已知限制與禁止事項：
#   1. 九篇論文為研究室 2026-08-15 提供的清單，不是完整著作列表。
#      新增論文請由後台操作，不要擴充本腳本 —— 它的定位是
#      「初始匯入」而非長期維護介面。
#   2. 禁止把本腳本的內容當成 legacy migration 證據。
#
# 維護契約：
#   若要修正某篇論文的書目資料，請直接於後台編輯；
#   本腳本只在全新資料庫初始化時使用。
#
# 驗證方式：
#   flask seed publications
#   pytest tests/test_publications_seed.py
# ============================================================

from __future__ import annotations

#: 九篇論文。書目資料於 2026-08-15 透過 Crossref API 查證，
#: 每一筆的 DOI、標題、期刊、年份與作者列均來自 Crossref 回應。
#:
#: 欄位說明：
#:   year        - 引用慣例採用的年份（期刊卷期年）
#:   authors     - Crossref 回傳的作者列（逐字）
#:   summary_zh  - 僅改寫標題與出處，不含未查證的數值或結論
PUBLICATIONS = [
    {
        "slug": "real-time-signal-quality-monitoring-ann-fpga",
        "output_type": "journal",
        "year": 2025,
        "doi": "10.1002/cta.4557",
        "title_en": (
            "Real-Time Monitoring of High-Speed Signal Quality Using Asynchronous "
            "Sampling, Amplitude Sorting, and Artificial Neural Network and Its "
            "FPGA Implementation"
        ),
        "title_zh": "以非同步取樣、振幅排序與類神經網路即時監測高速訊號品質及其 FPGA 實作",
        "venue": "International Journal of Circuit Theory and Applications",
        "authors": "Jau-Ji Jou, Chun-Liang Yang, Chih-Lung Tseng, Chun-Chen Yao, Jun-Yuan Zheng",
        "keywords": "光訊號品質監視, 非同步取樣, 振幅排序, 類神經網路, FPGA, NRZ",
        "summary_zh": (
            "本研究提出結合非同步取樣（asynchronous sampling）、振幅排序（amplitude sorting）"
            "與類神經網路的高速訊號品質即時監視方法，並將其實作於 FPGA。"
            "發表於 International Journal of Circuit Theory and Applications。"
        ),
    },
    {
        "slug": "performance-monitoring-nrz-machine-learning",
        "output_type": "conference",
        "year": 2021,
        "doi": "10.1109/ispacs51563.2021.9650979",
        "title_en": "Performance Monitoring of High-Speed NRZ Signals Using Machine Learning Techniques",
        "title_zh": "以機器學習技術監視高速 NRZ 訊號效能",
        "venue": (
            "2021 International Symposium on Intelligent Signal Processing "
            "and Communication Systems (ISPACS)"
        ),
        "authors": "Chun-Chen Yao, Jun-Yuan Zheng, Jau-Ji Jou, Chun-Liang Yang",
        "keywords": "效能監視, 機器學習, NRZ, 光通訊",
        "summary_zh": (
            "本會議論文探討以機器學習技術監視高速 NRZ 訊號的傳輸效能。"
            "發表於 2021 IEEE International Symposium on Intelligent Signal "
            "Processing and Communication Systems（ISPACS）。"
        ),
    },
    {
        "slug": "wdm-pon-protection-reconfigurable-optical-amplifiers",
        "output_type": "journal",
        "year": 2022,
        "doi": "10.3390/app12010365",
        "title_en": (
            "Protection Scheme for a Wavelength-Division-Multiplexed Passive Optical "
            "Network Based on Reconfigurable Optical Amplifiers"
        ),
        "title_zh": "以可重構光放大器為基礎的分波多工被動光網路保護機制",
        "venue": "Applied Sciences, 12(1), 365",
        "authors": "Hong-Sing Lee, Chun-Liang Yang, Chien-Hsiang Chou",
        "keywords": "WDM-PON, 光網路保護, 可重構光放大器, 被動光網路",
        "summary_zh": (
            "本研究提出以可重構光放大器（reconfigurable optical amplifiers）實現"
            "分波多工被動光網路（WDM-PON）的保護機制。"
            "發表於 Applied Sciences 第 12 卷第 1 期（線上首發 2021-12-31）。"
        ),
    },
    {
        "slug": "ream-wdm-pon-remote-amplification-fault-monitoring",
        "output_type": "journal",
        "year": 2012,
        "doi": "10.1364/jocn.4.000336",
        "title_en": (
            "Design and Demonstration of REAM-Based WDM-PONs With Remote Amplification "
            "and Channel Fault Monitoring"
        ),
        "title_zh": "具遠端放大與通道故障監視之 REAM 型 WDM-PON 設計與實證",
        "venue": "Journal of Optical Communications and Networking, 4(4), 336",
        "authors": "Shu-Chuan Lin, San-Liang Lee, Cheng-Kuang Liu, Chun-Liang Yang, Sun-Chien Ko, Ty-Wang Liaw",
        "keywords": "WDM-PON, REAM, 遠端放大, 通道故障監視",
        "summary_zh": (
            "本研究設計並實證以反射式電吸收調變器（REAM）為基礎的 WDM-PON，"
            "具備遠端放大與通道故障監視能力。"
            "發表於 Journal of Optical Communications and Networking。"
        ),
    },
    {
        "slug": "rsoa-wdm-pon-single-fabry-perot-etalon",
        "output_type": "journal",
        "year": 2011,
        "doi": "10.1002/mop.26178",
        "title_en": (
            "Performance Enhancement Scheme for RSOA-Based WDM-PONs by Using a "
            "Single Fabry-Perot Etalon"
        ),
        "title_zh": "以單一法布里–佩羅標準具提升 RSOA 型 WDM-PON 效能之方法",
        "venue": "Microwave and Optical Technology Letters",
        "authors": "Chun-Liang Yang, Ting-Lin Hsieh, Shu-Chuan Lin, Gerd Keiser, San-Liang Lee",
        "keywords": "RSOA, WDM-PON, Fabry-Perot etalon, 效能提升",
        "summary_zh": (
            "本研究提出以單一法布里–佩羅標準具（Fabry-Perot etalon）提升 RSOA 型 "
            "WDM-PON 效能的方法。發表於 Microwave and Optical Technology Letters。"
        ),
    },
    {
        "slug": "spectral-filtering-directly-modulated-channels-fp-etalon",
        "output_type": "journal",
        "year": 2009,
        "doi": "10.1364/jon.8.000306",
        "title_en": (
            "Spectral Filtering of Multiple Directly Modulated Channels for WDM Access "
            "Networks by Using an FP Etalon"
        ),
        "title_zh": "以 FP 標準具對 WDM 接取網路多路直接調變通道進行頻譜濾波",
        "venue": "Journal of Optical Networking, 8(3), 306",
        "authors": "Shu-Chuan Lin, San-Liang Lee, Chun-Liang Yang",
        "keywords": "頻譜濾波, 直接調變, WDM 接取網路, FP etalon",
        "summary_zh": (
            "本研究以法布里–佩羅標準具（FP etalon）對 WDM 接取網路中多路直接調變"
            "通道進行頻譜濾波。發表於 Journal of Optical Networking。"
        ),
    },
    {
        "slug": "simultaneous-channel-osnr-monitoring-polarization-selective-modulator",
        "output_type": "journal",
        "year": 2004,
        "doi": "10.1109/lpt.2004.823756",
        "title_en": (
            "Simultaneous Channel and OSNR Monitoring Using a Polarization-Selective "
            "Modulator and an LED"
        ),
        "title_zh": "以偏振選擇性調變器與 LED 同時監視通道與光訊雜比",
        "venue": "IEEE Photonics Technology Letters",
        "authors": "Chun-Liang Yang, San-Liang Lee, Hen-Wai Tsao, Jingshown Wu",
        "keywords": "OSNR 監視, 通道監視, 偏振選擇性調變器, 光效能監視",
        "summary_zh": (
            "本研究提出以偏振選擇性調變器搭配 LED，同時監視光通道與光訊雜比（OSNR）"
            "的方法。發表於 IEEE Photonics Technology Letters。"
        ),
    },
    {
        "slug": "optical-isolator-modules-monitoring-dwdm-tunable-lasers",
        "output_type": "journal",
        "year": 2004,
        "doi": "10.1364/jon.3.000452",
        "title_en": "Optical-Isolator-Based Modules for Monitoring DWDM Tunable Lasers",
        "title_zh": "以光隔離器為基礎之 DWDM 可調雷射監視模組",
        "venue": "Journal of Optical Networking, 3(6), 452",
        "authors": "Chun-Liang Yang, San-Liang Lee, Jingshown Wu",
        "keywords": "DWDM, 可調雷射, 波長監視, 光隔離器",
        "summary_zh": (
            "本研究提出以光隔離器為基礎的模組，用於監視 DWDM 可調雷射。"
            "發表於 Journal of Optical Networking。"
        ),
    },
    {
        "slug": "wavelength-control-dwdm-fabry-perot-etalon-optoelectronic-diode",
        "output_type": "journal",
        "year": 2004,
        "doi": "10.1364/ao.43.001914",
        "title_en": (
            "Wavelength Control of Tunable Dense Wavelength-Division Multiplexing Sources "
            "by Use of a Fabry-Perot Etalon and a Semiconductor Optoelectronic Diode"
        ),
        "title_zh": "以法布里–佩羅標準具與半導體光電二極體控制可調 DWDM 光源波長",
        "venue": "Applied Optics, 43(9), 1914",
        "authors": "Chun-Liang Yang, San-Liang Lee, Jingshown Wu",
        "keywords": "DWDM, 波長控制, Fabry-Perot etalon, 半導體光電二極體",
        "summary_zh": (
            "本研究以法布里–佩羅標準具（Fabry-Perot etalon）搭配半導體光電二極體，"
            "控制可調式高密度分波多工（DWDM）光源的波長。"
            "發表於 Applied Optics。"
        ),
    },
]


def _find_faculty():
    """取得教授 Person；不存在時回 None。"""
    from sqlalchemy import select

    from app.extensions import db
    from app.models.mixins import PersonStatus
    from app.models.person import Person

    return db.session.scalar(
        select(Person).where(Person.status == PersonStatus.FACULTY).order_by(Person.id)
    )


def _find_by_doi(doi: str):
    from sqlalchemy import select

    from app.extensions import db
    from app.models.research_output import ResearchOutput

    return db.session.scalar(select(ResearchOutput).where(ResearchOutput.doi == doi))


def run_seed(force: bool = False) -> list[str]:
    """匯入九篇已查證的論文，回傳供 CLI 顯示的訊息清單。

    Args:
        force: 已存在時仍更新書目欄位（預設略過）。
    """
    from app.extensions import db
    from app.models.research_output import ResearchOutput
    from app.services.research_service import ResearchService, ResearchServiceError

    messages: list[str] = []

    faculty = _find_faculty()
    if faculty is None:
        raise RuntimeError(
            "找不到教授（Person status=faculty）。\n"
            "論文必須關聯到教授才能形成 Person <-> ResearchOutput 的雙向導航"
            "（SAI §13.1）。請先執行：flask seed legacy"
        )

    messages.append(f"書目來源：Crossref API（2026-08-15 查證）")
    messages.append(f"關聯教授：{faculty.name_zh}（/people/{faculty.slug}）")
    messages.append("")

    created = skipped = published = failed = 0

    for entry in PUBLICATIONS:
        existing = _find_by_doi(entry["doi"])

        if existing is not None and not force:
            skipped += 1
            messages.append(f"  略過（已存在）：{entry['doi']}")
            continue

        data = {
            "output_type": entry["output_type"],
            "year": entry["year"],
            "slug": entry["slug"],
            "title_zh": entry["title_zh"],
            "title_en": entry["title_en"],
            "summary_zh": entry["summary_zh"],
            "venue": entry["venue"],
            "doi": entry["doi"],
            "authors_display_text": entry["authors"],
            "keywords": entry["keywords"],
            "people": [faculty.id],
        }

        try:
            if existing is not None:
                output = ResearchService.update(existing, data)
            else:
                output = ResearchService.create(data)
                created += 1
        except ResearchServiceError as exc:
            failed += 1
            messages.append(f"  ✗ 建立失敗 {entry['doi']}：{exc}")
            continue

        db.session.flush()

        try:
            ResearchService.publish(output)
            published += 1
            messages.append(f"  ✔ {entry['year']}  {entry['venue'][:46]}")
            messages.append(f"     /research/{output.slug}")
        except ResearchServiceError as exc:
            failed += 1
            messages.append(f"  ! 已建立但未發布 {entry['doi']}：{exc}")

    messages.append("")
    messages.append(f"新增 {created} 筆、略過 {skipped} 筆、發布 {published} 筆、失敗 {failed} 筆。")
    messages.append("")
    messages.append("後續（重要）：")
    messages.append("  這些摘要僅改寫自論文標題與出處，不含任何未查證的數值或結論。")
    messages.append("  請教授提供原始 abstract 後，於後台補齊每篇的")
    messages.append("  Problem / Method / Results / Significance（SAI §5.3）。")

    return messages
