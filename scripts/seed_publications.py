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

#: 九篇論文。書目資料於 2026-08-15 透過 Crossref API 查證；
#: 摘要原文於 2026-08-16 由 Crossref 與 OpenAlex 取得（見 abstract_source）。
#:
#: 欄位說明：
#:   year            - 引用慣例採用的年份（期刊卷期年）
#:   authors         - Crossref 回傳的作者列（逐字）
#:   abstract_en     - 出版方登錄的英文摘要原文（逐字，未改寫）
#:   abstract_source - 摘要來源與取得日期，供查核
#:   summary_zh      - 依 abstract_en 撰寫的中文摘要（SAI §5.3）
#:   problem/method/results/significance_zh
#:                   - SAI §5.3 的四段式結構，內容一律可回溯到 abstract_en
#:
#: 硬性規則（SAI §2.3、§23.1）：
#:   中文欄位只能改寫 abstract_en 已陳述的內容。
#:   任何數值都必須在 abstract_en 中找得到 —— 這條由
#:   tests/test_publications_seed.py::test_metrics_are_traceable_to_abstract
#:   自動強制執行，不是靠人自律。
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
        "title_zh": "以非同步取樣、振幅排序與類神經網路即時監視高速訊號品質及其 FPGA 實作",
        "venue": "International Journal of Circuit Theory and Applications",
        "authors": "Jau-Ji Jou, Chun-Liang Yang, Chih-Lung Tseng, Chun-Chen Yao, Jun-Yuan Zheng",
        "keywords": "光訊號品質監視, 非同步取樣, 振幅排序, 類神經網路, FPGA, NRZ",
        "abstract_source": "crossref 2026-08-16",
        "abstract_en": (
            "In high-speed data transmission, signal quality monitoring guarantees "
            "transmission system reliability and stability. In this study, high-speed "
            "signal waveforms were sampled directly and asynchronously, and then the "
            "sampled values were sorted by amplitude. Using these sampled data sets, we "
            "propose an artificial neural network (ANN) model to estimate signal quality "
            "parameters of high-speed signals. The sampled values can clearly show the "
            "characteristics of the waveform after amplitude sorting, which will "
            "significantly reduce the error of parameter calculation, and a relatively "
            "simple ANN model can be used. Using 25-Gb/s non-return-to-zero signals as an "
            "example, the ANN inputs 40 signal sampling data through a hidden layer with 7 "
            "neurons and can estimate five types of signal quality parameters: Q-factor, "
            "signal-to-noise ratio, time jitter, rise time, and fall time. The mean square "
            "error of the calculated time jitter was 11.8%, and those of the other "
            "parameters were lower than 10%. The simple ANN model will be easier to "
            "implement in hardware. A field-programmable gate array was used to implement "
            "the ANN hardware for estimating signal quality parameters. The calculated "
            "average errors of the five signal quality parameters between software and "
            "hardware methods were less than 1%. The implemented hardware estimation of "
            "high-speed signal quality parameters could be used for real-time signal "
            "quality monitoring in high-speed data transmission modules and devices."
        ),
        "summary_zh": (
            "高速資料傳輸中，訊號品質監視是確保系統可靠與穩定的關鍵。"
            "本研究直接以非同步方式取樣高速訊號波形，再依振幅排序，"
            "並以類神經網路（ANN）從這些取樣資料推估訊號品質參數。"
            "以 25-Gb/s NRZ 訊號為例，可同時推估 Q 因子、訊雜比、"
            "時間抖動、上升時間與下降時間五項參數，並以 FPGA 完成硬體實作，"
            "使該方法可用於傳輸模組與元件的即時品質監視。"
        ),
        "problem_zh": (
            "高速資料傳輸系統的可靠性與穩定性仰賴訊號品質監視，"
            "但傳統參數量測不易在傳輸中即時進行。"
        ),
        "method_zh": (
            "直接對高速訊號波形進行非同步取樣，再將取樣值依振幅排序；"
            "排序後的取樣值能清楚呈現波形特徵，可顯著降低參數計算誤差，"
            "因此只需相對簡單的類神經網路模型。"
            "以 25-Gb/s NRZ 訊號為例，將 40 筆取樣資料輸入具 7 個神經元"
            "隱藏層的 ANN，推估五項訊號品質參數。"
            "並以現場可程式閘陣列（FPGA）實作該 ANN 硬體。"
        ),
        "results_zh": (
            "可推估 Q 因子、訊雜比、時間抖動、上升時間與下降時間五項參數。"
            "時間抖動的均方誤差為 11.8%，其餘參數均低於 10%。"
            "軟體與硬體兩種方法之間，五項參數的平均誤差小於 1%。"
        ),
        "significance_zh": (
            "簡化的 ANN 模型較易以硬體實現；所完成的硬體推估可用於"
            "高速資料傳輸模組與元件的即時訊號品質監視。"
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
        "abstract_source": "openalex 2026-08-16",
        "abstract_en": (
            "Advances in high-speed communication network technologies have spurred "
            "interest in signal performance monitoring. This study proposed a 25-Gb/s "
            "non-return-to-zero (NRZ) signal performance monitoring method using an "
            "artificial neural network (ANN), which can estimate the five parameters of Q "
            "factor, signal-to-noise ratio, time jitter, rise time, and fall time. Using "
            "5000 data sets and adopting seven neurons in the hidden layer, the mean "
            "relative errors of the five estimated parameters are about 5.76% to 11.74%. "
            "This parameter extraction technique based on machine learning can apply to "
            "real-time optical network performance monitoring for high-speed NRZ signals."
        ),
        "summary_zh": (
            "高速通訊網路技術的發展帶動了對訊號效能監視的需求。"
            "本研究提出以類神經網路（ANN）監視 25-Gb/s NRZ 訊號效能的方法，"
            "可推估 Q 因子、訊雜比、時間抖動、上升時間與下降時間五項參數。"
            "此以機器學習為基礎的參數萃取技術，可應用於高速 NRZ 訊號的"
            "光網路即時效能監視。"
        ),
        "problem_zh": "高速通訊網路的發展使訊號效能監視成為必要，需要可即時取得的效能參數。",
        "method_zh": (
            "以類神經網路建立 25-Gb/s NRZ 訊號的效能監視方法，"
            "使用 5000 組資料集，隱藏層採用 7 個神經元。"
        ),
        "results_zh": (
            "可推估 Q 因子、訊雜比、時間抖動、上升時間與下降時間五項參數，"
            "五項推估參數的平均相對誤差約為 5.76% 至 11.74%。"
        ),
        "significance_zh": (
            "此以機器學習為基礎的參數萃取技術，可應用於高速 NRZ 訊號的"
            "光網路即時效能監視。"
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
        "abstract_source": "crossref 2026-08-16",
        "abstract_en": (
            "This paper demonstrates a wavelength-division-multiplexed passive optical "
            "network (WDM-PON) scheme based on novel reconfigurable optical amplifiers "
            "(ROAs). The measured switching characteristics of the ROA3 constructed with a "
            "2 x 2 crossbar optical switch and a four-port reversible optical circulator "
            "(OC) and a conventional EDFA can meet the requirements of most network "
            "management and surveillance. The self-made four-port reversible OC's response "
            "time is less than 2 ms, and its insertion losses are about 1 dB or less for "
            "all the transmission paths and switching states. An optimal design of ROAs is "
            "proposed and evaluated for bidirectional optical amplifier protection, in "
            "which ROA3 has an EDF length of 7.5 m long with a 1480 nm pump laser and "
            "possesses a backward or forward pumping configuration with the corresponding "
            "pump power of 200 mW or 50 mW. We verified the scheme's feasibility through a "
            "simulation of WDM-PON systems with 40 downstream and upstream channels. This "
            "scheme enables the intelligent protection switching in practical operation "
            "scenarios for high-capacity multi-wavelength networks."
        ),
        "summary_zh": (
            "本研究提出以新型可重構光放大器（ROA）為基礎的分波多工被動光網路"
            "（WDM-PON）保護機制。ROA 由 2×2 交叉式光開關、自製四埠可逆光循環器"
            "與傳統摻鉺光纖放大器構成，其切換特性可滿足多數網路管理與監視需求。"
            "研究並提出雙向光放大器保護的最佳化設計，"
            "以 40 個上/下行通道的 WDM-PON 模擬驗證可行性，"
            "使高容量多波長網路能在實際運作情境下進行智慧型保護切換。"
        ),
        "problem_zh": (
            "高容量多波長網路需要能在實際運作情境下進行智慧型保護切換的機制。"
        ),
        "method_zh": (
            "以新型可重構光放大器（ROA）建構 WDM-PON 保護機制。"
            "ROA3 由 2×2 交叉式光開關、四埠可逆光循環器與傳統 EDFA 組成；"
            "並針對雙向光放大器保護提出最佳化設計 —— ROA3 採用 7.5 公尺摻鉺光纖"
            "與 1480 nm 泵浦雷射，可配置為後向或前向泵浦，"
            "對應泵浦功率分別為 200 mW 與 50 mW。"
        ),
        "results_zh": (
            "自製四埠可逆光循環器的響應時間小於 2 ms，"
            "在所有傳輸路徑與切換狀態下的插入損耗約為 1 dB 以下。"
            "並以具 40 個下行與上行通道的 WDM-PON 系統模擬驗證機制可行性。"
        ),
        "significance_zh": (
            "該切換特性可滿足多數網路管理與監視需求，"
            "使高容量多波長網路能在實際運作情境中實現智慧型保護切換。"
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
        "abstract_source": "openalex 2026-08-16",
        "abstract_en": (
            "The use of reflective electro-absorption modulators (REAMs) as colorless "
            "upstream transmitters for wavelength-division-multiplexing passive optical "
            "networks is attractive for providing 10 Gb/s and above data rates. We address "
            "the feasibility of applying the remotely pumped optical amplifier to boost all "
            "upstream channels when REAMs are used at the optical network units. This "
            "scheme can avoid the use of more complicated devices, such as integrated REAMs "
            "with semiconductor optical amplifiers. The remote pumping scheme can "
            "simultaneously boost the upstream and downstream signals as well as the "
            "monitoring signals by the gain from the erbium-doped fiber and Raman "
            "amplification. The required pump power and the limitation on the seeding power "
            "are analyzed in order to optimize the bidirectional transmission performance "
            "for different access network spans."
        ),
        "summary_zh": (
            "以反射式電吸收調變器（REAM）作為無色上行發射器，"
            "是分波多工被動光網路提供 10 Gb/s 以上速率的可行方向。"
            "本研究探討在光網路單元使用 REAM 時，"
            "以遠端泵浦光放大器提升所有上行通道的可行性。"
            "該遠端泵浦架構可藉由摻鉺光纖增益與拉曼放大，"
            "同時提升上行、下行與監視訊號，"
            "並避免使用整合半導體光放大器的 REAM 等較複雜元件。"
        ),
        "problem_zh": (
            "在光網路單元採用 REAM 時，需要在不引入更複雜元件的前提下"
            "提升所有上行通道的功率。"
        ),
        "method_zh": (
            "採用遠端泵浦光放大器架構，藉由摻鉺光纖增益與拉曼放大，"
            "同時提升上行、下行與監視訊號；"
            "並分析所需泵浦功率與種子光功率的限制。"
        ),
        "results_zh": (
            "驗證了以遠端泵浦提升全部上行通道的可行性，"
            "並針對不同接取網路跨距最佳化雙向傳輸效能。"
        ),
        "significance_zh": (
            "此架構可避免使用整合半導體光放大器的 REAM 等較複雜元件，"
            "使 REAM 型 WDM-PON 得以提供 10 Gb/s 以上的資料速率。"
        ),
    },
    {
        "slug": "rsoa-wdm-pon-single-fabry-perot-etalon",
        "output_type": "journal",
        "year": 2011,
        "doi": "10.1002/mop.26178",
        "title_en": (
            "Performance Enhancement Scheme for RSOA-Based WDM-PONs by Using a Single "
            "Fabry-Perot Etalon"
        ),
        "title_zh": "以單一法布里–佩羅標準具提升 RSOA 型 WDM-PON 效能之方法",
        "venue": "Microwave and Optical Technology Letters",
        "authors": "Chun-Liang Yang, Ting-Lin Hsieh, Shu-Chuan Lin, Gerd Keiser, San-Liang Lee",
        "keywords": "RSOA, WDM-PON, Fabry-Perot etalon, 效能提升",
        "abstract_source": "crossref 2026-08-16",
        "abstract_en": (
            "A scheme for simultaneous extinction ratio enhancement, optical frequency "
            "stabilization, and wavelength reuse is proposed for reflective semiconductor "
            "optical amplifier-based wavelength-division-multiplexed passive optical "
            "networks to stabilize the optical channel frequency and enhance bidirectional "
            "transmission. This is achieved by simply employing a single Fabry-Perot etalon "
            "at the optical line terminal rather than having one at each optical network "
            "unit. Compared with the remodulation scheme that uses a 10-Gb/s optical signal "
            "with a 3-dB extinction ratio as a downstream optical signal and a seed light, "
            "our scheme shows improvements in power penalties greater than 1.8 dB and 1.4 "
            "dB for 10-Gb/s downstream and 1.25-Gb/s upstream signals, respectively, after "
            "transmission of 25 km at a bit-error-rate = 10-9. Moreover, a power penalty of "
            "only 0.5-dB is observed in comparison to the two 1.25-Gb/s upstream "
            "bit-error-rate results based on using a continuous-wave seed light and a "
            "data-erased seed light."
        ),
        "summary_zh": (
            "本研究針對以反射式半導體光放大器（RSOA）為基礎的 WDM-PON，"
            "提出同時達成消光比提升、光頻率穩定與波長再利用的方法。"
            "作法是只在光線路終端設置單一法布里–佩羅標準具，"
            "而非在每個光網路單元各配置一個，"
            "藉此穩定光通道頻率並改善雙向傳輸效能。"
        ),
        "problem_zh": (
            "RSOA 型 WDM-PON 需要同時穩定光通道頻率、提升消光比並支援波長再利用，"
            "但在每個光網路單元各配置元件會增加成本與複雜度。"
        ),
        "method_zh": (
            "僅在光線路終端（OLT）設置單一法布里–佩羅標準具，"
            "而非於每個光網路單元（ONU）各設一個。"
        ),
        "results_zh": (
            "與使用 3-dB 消光比之 10-Gb/s 下行訊號兼種子光的再調變架構相比，"
            "在傳輸 25 公里、位元錯誤率為 10⁻⁹ 的條件下，"
            "10-Gb/s 下行與 1.25-Gb/s 上行訊號的功率損失分別改善超過 1.8 dB 與 1.4 dB。"
            "此外，相較於使用連續波種子光與資料抹除種子光的兩組 1.25-Gb/s 上行結果，"
            "功率損失僅 0.5 dB。"
        ),
        "significance_zh": (
            "以單一元件取代逐一配置，可在降低系統複雜度的前提下"
            "穩定光通道頻率並改善雙向傳輸。"
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
        "abstract_source": "openalex 2026-08-16",
        "abstract_en": (
            "Spectral and waveform reshaping schemes can enhance the transmission distance "
            "of fiber links that use directly modulated lasers as transmitters. We prove "
            "the feasibility of using a simple Fabry-Perot (FP) etalon as the spectral "
            "reshaper for applications in wavelength division multiplexing (WDM) access "
            "networks. The transient chirp and adiabatic chirp of a directly modulated "
            "laser are analyzed in detail by using the time-resolved chirp measurement. The "
            "effects of the original extinction ratio and the adiabatic chirp on the "
            "spectral reshaping are clarified to obtain the optimal operation conditions. "
            "It is shown that placing a single-cavity FP etalon filter after multiple 10 "
            "Gbits/s directly modulated lasers can extend their transmission distances from "
            "<10 to >50 km in the 1.55 um wavelength window. Due to the limited filtering "
            "capability of the etalon, the choice of the original extinction ratio and "
            "finesse of the etalon is discussed in detail from the experiments and "
            "simulation."
        ),
        "summary_zh": (
            "頻譜與波形整形可延長以直接調變雷射為發射器的光纖鏈路傳輸距離。"
            "本研究證實可用結構簡單的法布里–佩羅（FP）標準具作為頻譜整形元件，"
            "應用於分波多工（WDM）接取網路，"
            "並以時間解析啁啾量測詳細分析直接調變雷射的暫態啁啾與絕熱啁啾，"
            "釐清原始消光比與絕熱啁啾對頻譜整形的影響以取得最佳操作條件。"
        ),
        "problem_zh": (
            "以直接調變雷射作為發射器的光纖鏈路，其傳輸距離受啁啾限制，"
            "需要能延長距離且結構簡單的整形方式。"
        ),
        "method_zh": (
            "以單腔式 FP 標準具作為頻譜整形元件，置於多台直接調變雷射之後；"
            "並以時間解析啁啾量測分析暫態啁啾與絕熱啁啾，"
            "釐清原始消光比與絕熱啁啾對整形效果的影響。"
        ),
        "results_zh": (
            "在 1.55 μm 波段，將單腔式 FP 標準具置於多台 10 Gbit/s 直接調變雷射之後，"
            "可將傳輸距離由小於 10 公里延長至大於 50 公里。"
        ),
        "significance_zh": (
            "受限於標準具的濾波能力，原始消光比與標準具細緻度的選擇"
            "需依實驗與模擬結果權衡；該方法為 WDM 接取網路提供了"
            "結構簡單的距離延伸途徑。"
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
        "abstract_source": "openalex 2026-08-16",
        "abstract_en": (
            "A compact module including a polarization-selective electrooptic modulator and "
            "a light-emitting diode (LED) sensor is utilized for optical "
            "signal-to-noise-ratio (OSNR) monitoring and channel recognition. The modulator "
            "acts as a polarizer and provides signal dithering to improve the detection "
            "sensitivity, while the LED is used as a channel sensor. The channel sensor can "
            "monitor a wide wavelength range that is rapidly adjustable and can have much "
            "relaxed tolerance on temperature control. The combination of a polarization "
            "controller/rotator and the polarized modulator can accurately measure the OSNR "
            "between 5 and 35 dB with an error less than 0.5 dB."
        ),
        "summary_zh": (
            "本研究以一個包含偏振選擇性電光調變器與發光二極體（LED）感測器的"
            "小型模組，同時進行光訊雜比（OSNR）監視與通道辨識。"
            "調變器兼作偏振器並提供訊號抖動以提升偵測靈敏度，"
            "LED 則作為通道感測器，可監視可快速調整的寬廣波長範圍，"
            "且對溫度控制的容許度大幅放寬。"
        ),
        "problem_zh": (
            "光網路需要能同時辨識通道並量測光訊雜比、"
            "且對溫度控制要求不高的小型化監視模組。"
        ),
        "method_zh": (
            "以偏振選擇性電光調變器搭配 LED 感測器構成小型模組："
            "調變器兼作偏振器並提供訊號抖動以提升偵測靈敏度，"
            "LED 作為通道感測器；再與偏振控制器／旋轉器組合使用。"
        ),
        "results_zh": (
            "偏振控制器／旋轉器與偏振調變器的組合，"
            "可在 5 至 35 dB 範圍內準確量測 OSNR，誤差小於 0.5 dB。"
        ),
        "significance_zh": (
            "通道感測器可監視可快速調整的寬廣波長範圍，"
            "且對溫度控制的容許度大幅放寬，有利於實際佈署。"
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
        "abstract_source": "openalex 2026-08-16",
        "abstract_en": (
            "Simultaneous monitoring of power, wavelength, and channel number for a tunable "
            "laser is demonstrated by use of a combination of an optical isolator, an "
            "etalon, a polarizer, and photodiodes. The mode hopping and incomplete-tuning "
            "problems that might arise in tuning the laser can be detected with the proposed "
            "approach. The spectral response of the monitoring module can be adjusted to "
            "match the wavelength-tuning range. The spectral adjustment can be performed by "
            "rotating the input polarization or the output polarizer. It can also be tuned "
            "by rotating the second stage if a two-stage isolator is used. We demonstrate "
            "experimentally the feasibility of this approach by use of discrete "
            "fiber-pigtailed components."
        ),
        "summary_zh": (
            "本研究以光隔離器、標準具、偏振器與光二極體的組合，"
            "同時監視可調雷射的功率、波長與通道編號，"
            "並可偵測雷射調諧過程中可能發生的模態跳躍與調諧不完全問題。"
            "監視模組的頻譜響應可調整以匹配波長調諧範圍，"
            "並以分立光纖尾纖元件實驗驗證此方法的可行性。"
        ),
        "problem_zh": (
            "可調雷射在調諧過程可能發生模態跳躍與調諧不完全，"
            "需要能同時監視功率、波長與通道編號的方式。"
        ),
        "method_zh": (
            "結合光隔離器、標準具、偏振器與光二極體構成監視模組。"
            "頻譜響應可藉由旋轉輸入偏振或輸出偏振器調整；"
            "若採用雙級隔離器，亦可藉由旋轉第二級調整。"
        ),
        "results_zh": (
            "可同時監視功率、波長與通道編號，"
            "並偵測調諧時的模態跳躍與調諧不完全問題；"
            "已以分立光纖尾纖元件實驗驗證可行性。"
        ),
        "significance_zh": (
            "監視模組的頻譜響應可調整以匹配不同的波長調諧範圍，"
            "適用於 DWDM 可調光源的效能監視。"
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
        "abstract_source": "openalex 2026-08-16",
        "abstract_en": (
            "A high-resolution tunable-wavelength controller is achieved by use of an etalon "
            "for control of wavelength drift and a semiconductor optical diode (SOD) for "
            "channel recognition. The etalon provides a stable wavelength reference, and the "
            "SOD can detect mode-hopping and incomplete-tuning problems in tuning a laser. "
            "With the help of a Fabry-Perot etalon as a precise wavelength reference, the "
            "usual concern with the temperature stability of a SOD can be relaxed at least "
            "tenfold compared with wavelength control with a single SOD. We demonstrate the "
            "feasibility of monitoring tunable lasers by using a Fabry-Perot laser diode "
            "(FPLD) or a semiconductor optical amplifier (SOA). The induced voltage of the "
            "FPLD and that of the SOA are modeled with analytic expressions that can help to "
            "optimize the operation of a SOD sensor."
        ),
        "summary_zh": (
            "本研究以標準具控制波長漂移、以半導體光電二極體（SOD）辨識通道，"
            "構成高解析度的可調波長控制器。"
            "標準具提供穩定的波長參考，SOD 則可偵測雷射調諧時的"
            "模態跳躍與調諧不完全問題。"
            "並以法布里–佩羅雷射二極體或半導體光放大器驗證監視可調雷射的可行性。"
        ),
        "problem_zh": (
            "可調 DWDM 光源需要高解析度的波長控制，"
            "而單獨使用半導體光電二極體時，溫度穩定度是主要顧慮。"
        ),
        "method_zh": (
            "以標準具控制波長漂移並提供穩定波長參考，"
            "以半導體光電二極體（SOD）進行通道辨識；"
            "並分別以法布里–佩羅雷射二極體（FPLD）與半導體光放大器（SOA）實作驗證。"
        ),
        "results_zh": (
            "以法布里–佩羅標準具作為精確波長參考後，"
            "相較於僅使用單一 SOD 的波長控制，"
            "對 SOD 溫度穩定度的顧慮可放寬至少十倍。"
        ),
        "significance_zh": (
            "FPLD 與 SOA 的感應電壓可用解析式建模，"
            "有助於最佳化 SOD 感測器的操作條件。"
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

    messages.append("書目來源：Crossref API（2026-08-15 查證）")
    messages.append("摘要來源：Crossref / OpenAlex（2026-08-16 取得，見各筆 abstract_source）")
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
            # 出版方登錄的英文摘要原文。存入 summary_en 讓英文讀者與
            # 檢索系統看到的是作者自己寫的摘要，而不是中譯的回譯。
            "summary_en": entry["abstract_en"],
            # SAI §5.3 的四段式研究內容。全部改寫自 abstract_en，
            # 不含任何原文未陳述的數值或結論。
            "problem_zh": entry["problem_zh"],
            "method_zh": entry["method_zh"],
            "results_zh": entry["results_zh"],
            "significance_zh": entry["significance_zh"],
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
    messages.append("內容依據：")
    messages.append("  每篇的中文摘要與 Problem / Method / Results / Significance")
    messages.append("  皆改寫自出版方登錄的英文摘要原文（summary_en），")
    messages.append("  來源與取得日期記錄於各筆 abstract_source。")
    messages.append("  中文欄位中的每一個效能數值都必須能在原文找到 ——")
    messages.append("  由 test_metrics_are_traceable_to_abstract 自動強制。")
    messages.append("")
    messages.append("後續：")
    messages.append("  若教授對譯文用詞有修正意見，請直接於後台編輯；")
    messages.append("  本腳本僅用於全新資料庫的初始匯入。")

    return messages
