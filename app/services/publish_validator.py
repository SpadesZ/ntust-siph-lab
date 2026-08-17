# ============================================================
# NTUST SiPh Lab - Publish Gate Validator
#
# 上下游：
#   blueprints/admin/routes.py（發布動作）
#       -> PublishValidator.validate_person / validate_research
#       -> ValidationResult -> flash 錯誤或允許發布
#   PersonService / ResearchService -> 發布前呼叫
#   Admin Dashboard -> 顯示 blocking 與 warning 數量
#
# 檔案路徑：
#   app/services/publish_validator.py
#
# 建立日期：2026-08-14
# 最後重大修改：2026-08-14
# 版本：v1.0
#
# 模組定位與責任邊界：
#   實作 SAI §15「發布門檻」、附錄 C「Publish Checklist」與
#   skills/siph-lab-seo-geo 的 Publish gate。這是「草稿可以隨便，
#   但公開內容必須合格」這條界線的唯一守門員。
#
#   為什麼要獨立成模組：
#     發布檢查同時被三處使用 —— admin 發布按鈕、批次 seed script、
#     Dashboard 品質提醒。若分散實作，三處標準會漂移，
#     最終導致「Dashboard 說沒問題但發布被擋」。
#
#   責任邊界（不得做的事）：
#     - 不得修改資料（純檢查，無副作用）。
#     - 不得 render HTML 或 flash（回傳結構化結果由呼叫端決定呈現）。
#
# 輸入 -> 處理 -> 輸出 Pipeline：
#   輸入：Person 或 ResearchOutput 實體
#   處理：逐條檢查 SAI §15 的發布門檻
#   輸出：ValidationResult（errors 阻擋發布 / warnings 僅提醒）
#
# 主要 Class / Function：
#   ValidationIssue    - 單一問題（欄位、訊息、嚴重度）
#   ValidationResult   - 檢查結果（is_valid / errors / warnings）
#   PublishValidator.validate_person(person)
#   PublishValidator.validate_research(output)
#
# 依賴套件：
#   app.models、app.utils.validators
#
# 環境變數：無。
# 資料庫使用方式：只讀傳入的實體，不查詢也不寫入。
#
# Error Handling / Fallback：
#   本模組不拋例外。所有問題都以 ValidationIssue 回報，
#   因為「內容不合格」是正常業務狀態，不是程式錯誤。
#
# 特殊機制（errors vs warnings 的判準）：
#   errors（阻擋發布）：會造成公開頁「壞掉」或「誤導」的問題。
#     例如缺標題（頁面無 H1）、有圖無 alt（違反 a11y 與 AC-12）、
#     外部連結格式錯誤（產生壞連結）。
#   warnings（不阻擋）：影響品質但頁面仍可用且不誤導。
#     例如缺英文標題、缺 Method/Results。
#
#   為什麼不把所有問題都設為 errors：
#     SAI §7.2 明確要求 Needs attention「不阻擋草稿」，
#     且過嚴的門檻會逼使管理者填入敷衍內容 —— 那比留空更糟，
#     因為虛構內容違反 §2.3 與 GEO contract。
#
# 已知限制與禁止事項：
#   1. 本模組無法驗證「內容是否為真」。DOI 可達性、外鏈有效性
#      需由 scripts/verify_migration.py 做 HTTP 檢查。
#   2. 禁止為了讓某筆資料通過而放寬全域規則；
#      特例應以明確的旗標處理（如 legacy_pending_detail）。
#
# 維護契約：
#   1. 新增檢查項目時，必須明確判定是 error 還是 warning，
#      並在 docs/acceptance-checklist.md 同步說明。
#   2. legacy_pending_detail 的豁免只適用「研究焦點」一項，
#      不得擴大到姓名、slug 或照片 alt。
#
# 驗證方式：
#   pytest tests/test_publish_validator.py
#   pytest tests/test_people.py::test_publish_requires_photo_alt
# ============================================================

from __future__ import annotations

from dataclasses import dataclass, field

from app.models.mixins import PersonStatus, PublishStatus
from app.models.person import Person
from app.models.research_output import ResearchOutput
from app.utils.validators import is_valid_email, is_valid_url

#: 嚴重度。
SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"


@dataclass(frozen=True)
class ValidationIssue:
    """單一驗證問題。"""

    field: str
    message: str
    severity: str = SEVERITY_ERROR


@dataclass
class ValidationResult:
    """驗證結果集合。"""

    issues: list[ValidationIssue] = field(default_factory=list)

    def add_error(self, field_name: str, message: str) -> None:
        self.issues.append(ValidationIssue(field_name, message, SEVERITY_ERROR))

    def add_warning(self, field_name: str, message: str) -> None:
        self.issues.append(ValidationIssue(field_name, message, SEVERITY_WARNING))

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == SEVERITY_ERROR]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == SEVERITY_WARNING]

    @property
    def is_valid(self) -> bool:
        """是否允許發布（只有 error 會阻擋）。"""
        return not self.errors

    def error_messages(self) -> list[str]:
        return [i.message for i in self.errors]

    def warning_messages(self) -> list[str]:
        return [i.message for i in self.warnings]


class PublishValidator:
    """發布門檻檢查（SAI §15、附錄 C）。"""

    # ------------------------------------------------------------------
    # 共用檢查
    # ------------------------------------------------------------------
    @staticmethod
    def _check_urls(result: ValidationResult, pairs: list[tuple[str, str | None, str]]) -> None:
        """批次檢查 URL 欄位格式。

        壞掉的外部連結是 error 而非 warning：
        它會在公開頁產生一個點了會失敗的連結，
        直接違反 SAI §12.1「舊 URL 不造成無意義 404」的精神
        與 skills/siph-lab-seo-geo 的 publish gate。
        """
        for field_name, value, label in pairs:
            if value and not is_valid_url(value):
                result.add_error(field_name, f"{label} 不是有效的 http/https 網址。")

    # ------------------------------------------------------------------
    # Person
    # ------------------------------------------------------------------
    @staticmethod
    def validate_person(person: Person) -> ValidationResult:
        """人物發布門檻（SAI §15.1、§15.3、附錄 C）。"""
        result = ValidationResult()

        # --- 身分（error）---
        if not (person.name_zh or "").strip():
            result.add_error("name_zh", "中文姓名為必填。")

        if not (person.slug or "").strip():
            result.add_error("slug", "slug 為必填且必須唯一。")

        if person.status not in PersonStatus.ALL:
            result.add_error("status", "人物狀態不合法。")

        # --- 研究焦點 ---
        has_focus = bool(person.research_focus_zh or person.research_focus_en)
        if not has_focus:
            if person.legacy_pending_detail:
                # 母站遷入且尚未補齊者：依 2026-08-16 管理者裁示豁免
                # （docs/adr/ADR-012-legacy-pending-publish-exemption.md），
                # 但必須持續以 warning 提醒（見檔頭維護契約）。
                # 豁免僅限研究焦點一項，不得擴及姓名/slug/照片 alt。
                result.add_warning(
                    "research_focus_zh",
                    "此人物由母站遷入，研究焦點尚未補齊（已依核准豁免發布門檻，"
                    "請儘速補齊以符合 SAI §15.1）。",
                )
            else:
                result.add_error(
                    "research_focus_zh", "研究焦點至少需填寫一個語言版本（SAI §15.1）。"
                )

        # --- 照片與 alt（error，對應 AC-12）---
        if person.photo_path and not (person.photo_alt_zh or "").strip():
            result.add_error(
                "photo_alt_zh", "已上傳照片時，替代文字（alt）為必填（SAI §16、AC-12）。"
            )

        # --- 畢業生額外門檻（SAI §15.3）---
        if person.status == PersonStatus.ALUMNI:
            if not person.graduation_year:
                result.add_warning(
                    "graduation_year", "畢業生建議填寫畢業年度，否則不會出現在年度分組中。"
                )
            if not (person.thesis_title_zh or person.thesis_title_en):
                result.add_warning("thesis_title_zh", "畢業生建議填寫論文題目。")

            # 隱私：填了就業資訊卻沒勾選公開 -> 提醒（不會顯示）。
            if (person.current_affiliation or person.current_position) and not person.destination_public:
                result.add_warning(
                    "destination_public",
                    "已填寫畢業去向但未勾選「可公開」，前台不會顯示此資訊（SAI §5.4 隱私規則）。",
                )

        # --- 連結格式（error）---
        PublishValidator._check_urls(
            result,
            [
                ("orcid_url", person.orcid_url, "ORCID"),
                ("scholar_url", person.scholar_url, "Google Scholar"),
                ("github_url", person.github_url, "GitHub"),
                ("linkedin_url", person.linkedin_url, "LinkedIn"),
                ("external_url", person.external_url, "外部連結"),
            ],
        )

        if person.email_public and not is_valid_email(person.email_public):
            result.add_error("email_public", "公開 Email 格式不正確。")

        # --- SEO 品質（warning）---
        if not person.name_en:
            result.add_warning("name_en", "建議填寫英文姓名，有助於英文檢索與 Person 結構化資料。")

        return result

    # ------------------------------------------------------------------
    # ResearchOutput
    # ------------------------------------------------------------------
    @staticmethod
    def validate_research(output: ResearchOutput) -> ValidationResult:
        """成果發布門檻（SAI §15.2、附錄 C）。"""
        result = ValidationResult()

        # --- 識別（error）---
        if not (output.slug or "").strip():
            result.add_error("slug", "slug 為必填且必須唯一。")

        if not (output.title_zh or output.title_en):
            result.add_error("title_zh", "標題至少需填寫一個語言版本（SAI §15.2）。")

        if not output.year:
            result.add_error("year", "年份為必填。")

        if not output.output_type:
            result.add_error("output_type", "成果類型為必填。")

        # --- 摘要（error）：這是 SEO/GEO 最核心的內容單位 ---
        if not (output.summary_zh or output.summary_en):
            result.add_error(
                "summary_zh",
                "摘要至少需填寫一個語言版本；成果頁必須讓讀者理解 "
                "Problem / Method / Result / Significance（SAI §5.3、§15.2）。",
            )

        # --- 主圖 alt（error，AC-12）---
        if output.hero_image_path and not (output.hero_image_alt_zh or "").strip():
            result.add_error(
                "hero_image_alt_zh", "已上傳主圖時，替代文字（alt）為必填（SAI §16、AC-12）。"
            )

        # --- featured 前提（error）---
        # SAI §15.2：published 才能 featured。
        if output.is_featured and output.publish_status != PublishStatus.PUBLISHED:
            result.add_error(
                "is_featured", "只有已發布的成果才能設為精選（SAI §15.2）。"
            )

        # --- 連結格式（error）---
        PublishValidator._check_urls(
            result,
            [
                ("external_url", output.external_url, "外部連結"),
                ("github_url", output.github_url, "GitHub"),
                ("dataset_url", output.dataset_url, "Dataset 連結"),
            ],
        )

        # --- DOI 格式（error）---
        # 注意：這裡檢查的是「已存入的值是否為正規化裸 DOI」。
        # 表單層會先呼叫 normalize_doi；若存入值仍不合格，
        # 表示有程式路徑繞過了正規化。
        if output.doi:
            if ResearchOutput.normalize_doi(output.doi) != output.doi:
                result.add_error(
                    "doi", "DOI 格式不正確或未正規化（應為 10.xxxx/yyyy 形式）。"
                )

        # --- 研究內容完整度（warning）---
        # SAI §15.2：論文/代表成果「建議」至少 Method + Results，
        # 用詞是建議，故為 warning。
        if not output.has_research_body:
            result.add_warning(
                "method_zh",
                "建議填寫 Method 與 Key results，否則成果頁無法回答 "
                "GEO contract 要求的可答結構（SAI §13.1）。",
            )

        if output.is_scholarly and not output.venue:
            result.add_warning("venue", "期刊/會議論文建議填寫 venue（期刊或會議名稱）。")

        if output.is_scholarly and not output.doi and not output.external_url:
            result.add_warning(
                "doi", "期刊/會議論文建議提供 DOI 或出版社連結以利查證（SAI §13.1 Sourceability）。"
            )

        if not output.title_en:
            result.add_warning("title_en", "建議填寫英文標題，有助於國際檢索。")

        if not output.keywords:
            result.add_warning("keywords_json", "建議填寫研究關鍵字。")

        # --- 關聯人物（warning）---
        if not output.person_links and not output.authors_display_text:
            result.add_warning(
                "authors_display_text",
                "建議關聯 Lab 成員或填寫作者列，否則成果頁缺少實體關聯（SAI §13.1）。",
            )

        return result
