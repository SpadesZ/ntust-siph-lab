# ============================================================
# NTUST SiPh Lab - Admin Forms
#
# 上下游：
#   Admin Route -> Form(request.form) -> validate_on_submit()
#       -> form.to_dict() -> PersonService / ResearchService / SettingsService
#   Form -> templates/admin/*.html（欄位 render 與錯誤顯示）
#   FlaskForm -> CSRFProtect（token 自動注入）
#
# 檔案路徑：
#   app/blueprints/admin/forms.py
#
# 建立日期：2026-08-14
# 最後重大修改：2026-08-14
# 版本：v1.0
#
# 模組定位與責任邊界（SAI §9.1 Layering）：
#   Form 負責「欄位驗證、CSRF、URL/slug/file constraint」，
#   明確「不得直接 commit DB」。
#
#   本檔與 utils/validators.py 的分工：
#     Form        -> 必填、長度、選項合法性、檔案類型（使用者輸入層）
#     validators  -> 正規化與格式判斷（可被 service/script 重用）
#     Service     -> 業務規則（唯一性、狀態轉換、交易）
#   三層都存在的理由：seed script 不經過 Form，仍需正規化與
#   業務規則；若把驗證全塞在 Form，批次匯入就會繞過所有檢查。
#
# 輸入 -> 處理 -> 輸出 Pipeline：
#   request.form -> WTForms 型別轉換與驗證
#     -> to_dict() 轉為 service 可接受的欄位 dict
#
# 主要 Class：
#   PersonForm         - 人物新增/編輯（SAI §15.1、§15.3）
#   GraduateForm       - 轉為畢業生（SAI §7.5）
#   ResearchForm       - 成果新增/編輯（SAI §15.2）
#   SiteSettingForm    - 網站設定（SAI §15.4）
#   ChangePasswordForm - 修改管理員密碼（SAI §7.3 System）
#   MediaUploadForm    - 圖片上傳（SAI §16）
#   ConfirmForm        - 純 CSRF 的確認表單（發布/封存等動作）
#
# 依賴套件：
#   flask-wtf, wtforms, app.models.mixins（選項來源）
#
# 環境變數：
#   間接使用 ALLOWED_IMAGE_EXTENSIONS（透過 FileAllowed）。
#
# 資料庫使用方式：
#   不存取。選項清單來自 mixins 常數；人物多選清單由 route
#   注入 choices（避免 Form 依賴 DB）。
#
# Error Handling / Fallback：
#   驗證錯誤累積在 form.errors，由 template 顯示於對應欄位旁
#   （SAI §17 Forms：error message 指向欄位且有文字）。
#   本檔不拋例外。
#
# 特殊機制（Optional() 的必要性）：
#   WTForms 對空字串的預設行為是「通過 Optional 但送出 ''」。
#   所有選填欄位都明確加上 Optional()，否則 DataRequired 以外的
#   validator（如 URL()）會對空值報錯，造成「不填也不行」。
#
# 特殊機制（多值欄位）：
#   research_focus / lab_proof / social_links / people 這些
#   可變長度的結構，WTForms 的 FieldList 在動態新增列時
#   與純 HTML 表單配合不佳。改由 route 直接解析
#   request.form.getlist()，並在此提供 parse_* 靜態方法，
#   讓解析邏輯仍集中在 form 層而非散落在 route。
#
# 已知限制與禁止事項：
#   1. 禁止在 Form 中查詢或寫入資料庫。
#   2. 禁止移除 FileAllowed —— 那是上傳安全的第一道檢查
#      （AC-11）。
#   3. 禁止把 slug 設為 DataRequired：留空時由 service 自動產生
#      （SAI §15.1 的 slug 欄位允許自動建議）。
#
# 維護契約：
#   新增欄位時必須同時更新：
#     (a) 對應的 Form 欄位
#     (b) to_dict()
#     (c) service 的 _assign_fields
#     (d) template
#   缺任何一項都會造成「填了但沒存」或「存了但看不到」。
#
# 驗證方式：
#   pytest tests/test_admin_forms.py
# ============================================================

from __future__ import annotations

from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField
from wtforms import (
    BooleanField,
    IntegerField,
    PasswordField,
    SelectField,
    StringField,
    SubmitField,
    TextAreaField,
)
from wtforms.validators import DataRequired, EqualTo, Length, NumberRange, Optional, ValidationError

from app.models.mixins import ContributorRole, OutputType, PersonStatus, PublishStatus
from app.models.research_output import ResearchOutput
from app.utils.validators import is_valid_email, is_valid_url


def _strip(value):
    """WTForms filter：送進驗證器之前先去掉前後空白。

    為什麼需要：
      瀏覽器的 HTML5 required 只檢查「非空字串」，因此
      「   」（純空白）會通過前端驗證並送出，
      再由伺服器端的 DataRequired（會 strip）判定失敗。

      結果是使用者填了看起來有東西的欄位卻被退回，
      而且送出前完全沒有提示。加上這個 filter 之後，
      表單層與瀏覽器對「有沒有填」的判斷一致，
      同時也讓所有欄位的前後空白不會進到資料庫。
    """
    return value.strip() if isinstance(value, str) else value


class AdminForm(FlaskForm):
    """後台表單共同基底：把 WTForms 的內建訊息切換為繁體中文。

    為什麼需要：
      整個後台介面是繁體中文，但只有 4 個欄位帶了 message=，
      其餘約 44 個 Length / NumberRange / DataRequired 都會落回
      WTForms 的英文預設值 —— 使用者實際看到的是
      「This field is required.」夾在一片中文裡。

      逐欄位補 message= 需要改四十多處，且日後新增欄位一定會漏。
      改用 WTForms 內建的 locale 機制，一次覆蓋全部驗證器，
      新欄位自動適用。

    為什麼不是設在 app 層級：
      這是後台表單的呈現語言，與公開站的 i18n（app/i18n.py）
      是不同的關注點；公開站不使用 WTForms。
    """

    class Meta:
        #: WTForms 內建 zh_TW 與 zh 翻譯檔；找不到對應字串時
        #: 自動回落英文，不會因缺翻譯而讓表單壞掉。
        locales = ["zh_TW", "zh"]

        def bind_field(self, form, unbound_field, options):
            """統一為所有文字欄位加上 strip filter（見 _strip）。

            為什麼在這裡做而不是逐欄位加 filters=：
              後台超過 80 個欄位，逐一加不但改動巨大，
              日後新增欄位也一定會漏 —— 而漏掉的症狀
              （空白字元繞過必填）不會有任何明顯跡象。

            PasswordField 明確排除：密碼的前後空白是使用者
            實際輸入的一部分，擅自去掉會讓既有密碼登入失敗。
            """
            if not isinstance(unbound_field.field_class, type) or not issubclass(
                unbound_field.field_class, PasswordField
            ):
                filters = list(unbound_field.kwargs.get("filters", ()))
                if _strip not in filters:
                    filters.append(_strip)
                unbound_field.kwargs["filters"] = filters

            return unbound_field.bind(form=form, **options)


class SafeUrl:
    """URL 格式與 scheme 驗證（SAI §15.1 Links、§15.2 Publication）。

    為什麼需要自訂 validator 而不用 wtforms.validators.URL：
      1. wtforms 的 URL validator 不限制 scheme，`javascript:alert(1)`
         會通過 —— 那個值若被寫入 href，點擊即觸發 XSS。
      2. 本專案允許管理者直接貼裸網域（"example.com"），
         wtforms 的 URL validator 會拒絕。

    為什麼「表單層」也要驗證（service 層已經會正規化）：
      service 的 normalize_url 對非法值回傳 None，也就是把輸入
      靜默丟棄。管理者貼錯連結後只會發現欄位變空白，
      沒有任何錯誤訊息，也不知道自己貼錯了。
      SAI §15.1 要求「格式檢查」，指的正是這種使用者可見的回饋。
    """

    def __init__(self, message: str | None = None) -> None:
        self.message = message or "請輸入有效的網址（僅接受 http/https）。"

    def __call__(self, form, field) -> None:
        if not field.data:
            return
        if not is_valid_url(field.data):
            raise ValidationError(self.message)


class SafeEmail:
    """Email 格式驗證（SAI §15.1）。

    不使用 wtforms.validators.Email：它需要額外的 email_validator
    相依，且對本案的需求（單純格式檢查）過重。
    與 app.utils.validators.is_valid_email 共用同一套規則，
    確保表單層與 service 層判斷一致。
    """

    def __init__(self, message: str | None = None) -> None:
        self.message = message or "Email 格式不正確。"

    def __call__(self, form, field) -> None:
        if not field.data:
            return
        if not is_valid_email(field.data):
            raise ValidationError(self.message)


class IsoDate:
    """日期必須是實際存在的 YYYY-MM-DD（SAI §8.4 publication_date）。

    為什麼需要：
      publication_date 在表單層原本只有 Length(max=10)，
      `2025-13-45`、`abcdefghij` 都會通過，然後在
      ResearchService._parse_publication_date 被靜默轉成 None ——
      管理者看到欄位變空白，卻沒有任何錯誤訊息，
      與 SafeUrl docstring 描述的是同一種缺陷。

      年份錯誤在學術網站的代價很高（引用資訊會錯），
      因此寧可擋下來要求更正，也不要默默丟掉。
    """

    def __init__(self, message: str | None = None) -> None:
        self.message = message or "日期格式須為 YYYY-MM-DD，且必須是實際存在的日期。"

    def __call__(self, form, field) -> None:
        if not field.data:
            return
        from datetime import date as _date

        try:
            _date.fromisoformat(str(field.data).strip())
        except ValueError as exc:
            raise ValidationError(self.message) from exc


class NormalisableDoi:
    """DOI 必須可被正規化為裸 DOI（SAI §15.2「DOI 格式 normalization」）。

    接受多種輸入形式（裸值、doi: 前綴、https://doi.org/ 完整 URL），
    但無法解析出 10.xxxx/yyyy 結構時回報錯誤 ——
    否則錯誤的 DOI 會靜默變成 None，管理者以為已填寫。
    """

    def __init__(self, message: str | None = None) -> None:
        self.message = message or "DOI 格式不正確（應為 10.xxxx/yyyy 形式）。"

    def __call__(self, form, field) -> None:
        if not field.data:
            return
        if ResearchOutput.normalize_doi(field.data) is None:
            raise ValidationError(self.message)

#: 上傳允許的副檔名。與 config.ALLOWED_IMAGE_EXTENSIONS 一致；
#: 在此重複宣告是因為 FileAllowed 需要在 class 定義時取值，
#: 而該時點沒有 app context。兩者不一致時以 MediaService 為準
#: （它有 Pillow 解碼這道更強的檢查）。
_IMAGE_EXTENSIONS = ["jpg", "jpeg", "png", "webp"]

_IMAGE_MESSAGE = "只允許 jpg / jpeg / png / webp 圖片（SAI §16）。"

#: <input type="file"> 的 accept 屬性。
#:
#: 為什麼要有：沒有 accept 時，作業系統的檔案選擇視窗會列出所有檔案，
#: 管理者可能選了 .heic（iPhone 預設格式）或 .pdf，按下儲存才被伺服器
#: 退回。accept 讓不合格的檔案在選擇的當下就是灰的。
#:
#: 這只是提示，不是防線 —— 它可以被繞過，因此伺服器端的
#: FileAllowed 與 MediaService 的驗證一個都不能省（SAI §16）。
#: 內容必須與 _IMAGE_EXTENSIONS 一致，否則會出現
#: 「選得到但存不了」或「存得了卻選不到」。
_IMAGE_ACCEPT = ",".join(f".{ext}" for ext in _IMAGE_EXTENSIONS)


class ConfirmForm(AdminForm):
    """僅含 CSRF token 的確認表單。

    用於發布、取消發布、封存、精選、刪除照片等「不需要輸入
    但必須防 CSRF」的 POST 動作（SAI §11.1）。
    """

    submit = SubmitField("確認")


class PersonForm(AdminForm):
    """人物新增/編輯表單（SAI §15.1、§15.3）。"""

    # --- Identity ---
    name_zh = StringField(
        "中文姓名 *",
        validators=[DataRequired(message="中文姓名為必填。"), Length(max=120)],
    )
    name_en = StringField("英文姓名", validators=[Optional(), Length(max=160)])
    slug = StringField(
        "網址 slug",
        validators=[Optional(), Length(max=110)],
        description="留空時由姓名自動產生。發布後變更會自動建立 301 轉址。",
    )
    status = SelectField(
        "身分 *",
        choices=[
            (PersonStatus.FACULTY, "教授 Faculty"),
            (PersonStatus.CURRENT, "在學研究生 Current"),
            (PersonStatus.ALUMNI, "畢業生 Alumni"),
        ],
        validators=[DataRequired()],
    )

    # --- Academic ---
    title_zh = StringField("職稱／年級（中）", validators=[Optional(), Length(max=120)])
    title_en = StringField("職稱／年級（英）", validators=[Optional(), Length(max=160)])
    degree = StringField("學位", validators=[Optional(), Length(max=80)])
    education_zh = TextAreaField("學歷（中）", validators=[Optional()])
    education_en = TextAreaField("學歷（英）", validators=[Optional()])
    entry_year = IntegerField(
        "入學年度", validators=[Optional(), NumberRange(min=1900, max=2200)]
    )
    graduation_year = IntegerField(
        "畢業年度", validators=[Optional(), NumberRange(min=1900, max=2200)]
    )

    # --- Research ---
    research_focus_zh = TextAreaField("研究焦點（中）", validators=[Optional()])
    research_focus_en = TextAreaField("研究焦點（英）", validators=[Optional()])
    thesis_title_zh = TextAreaField("論文題目（中）", validators=[Optional()])
    thesis_title_en = TextAreaField("論文題目（英）", validators=[Optional()])
    skills = StringField(
        "研究技能／主題標籤",
        validators=[Optional()],
        description="以逗號分隔，例如：矽光子, 光通訊, 機器學習",
    )
    bio_zh = TextAreaField("簡介（中）", validators=[Optional()])
    bio_en = TextAreaField("簡介（英）", validators=[Optional()])

    # --- Destination（畢業去向，隱私敏感）---
    current_affiliation = StringField(
        "目前單位", validators=[Optional(), Length(max=200)]
    )
    current_position = StringField("目前職稱", validators=[Optional(), Length(max=160)])
    destination_public = BooleanField(
        "已確認可公開畢業去向",
        description="未勾選時前台不會顯示上述兩欄（SAI §5.4 隱私規則）。",
    )

    # --- Links ---
    email_public = StringField(
        "公開 Email", validators=[Optional(), Length(max=200), SafeEmail()]
    )
    orcid_url = StringField("ORCID", validators=[Optional(), Length(max=255), SafeUrl()])
    scholar_url = StringField(
        "Google Scholar", validators=[Optional(), Length(max=255), SafeUrl()]
    )
    github_url = StringField("GitHub", validators=[Optional(), Length(max=255), SafeUrl()])
    linkedin_url = StringField("LinkedIn", validators=[Optional(), Length(max=255), SafeUrl()])
    external_url = StringField(
        "其他外部連結", validators=[Optional(), Length(max=500), SafeUrl()]
    )
    external_url_label = StringField(
        "外部連結顯示名稱", validators=[Optional(), Length(max=120)]
    )

    # --- Photo ---
    photo = FileField(
        "照片",
        validators=[Optional(), FileAllowed(_IMAGE_EXTENSIONS, _IMAGE_MESSAGE)],
        render_kw={"accept": _IMAGE_ACCEPT},
    )
    photo_alt_zh = StringField(
        "照片替代文字（中）",
        validators=[Optional(), Length(max=200)],
        description="有照片時為必填（SAI §16、AC-12）。",
    )
    photo_alt_en = StringField(
        "照片替代文字（英）",
        validators=[Optional(), Length(max=200)],
        description="選填。留空時英文版會沿用中文版並標記為中文（WCAG 3.1.2）。",
    )

    # --- SEO ---
    # seo_title_zh / seo_description_zh 已從表單移除：
    # 14 筆實際資料（5 位成員 + 9 篇成果）全部沒有填過，
    # 而 SEOService 由姓名與研究焦點自動生成的標題與描述已足夠。
    # 保留 DB 欄位與既有資料，SEOService 仍優先採用（若日後有值）。

    # --- Ordering ---
    sort_order = IntegerField("排序值", validators=[Optional(), NumberRange(min=0, max=99999)])
    is_featured = BooleanField("在首頁精選顯示")

    submit = SubmitField("儲存")

    def to_dict(self) -> dict:
        """轉為 PersonService 可接受的欄位 dict。

        注意：不包含 photo（檔案由 route 另行處理，
        因為上傳是獨立的交易步驟，見 PersonService.attach_photo）。
        """
        return {
            "name_zh": self.name_zh.data,
            "name_en": self.name_en.data,
            "slug": self.slug.data,
            "status": self.status.data,
            "title_zh": self.title_zh.data,
            "title_en": self.title_en.data,
            "degree": self.degree.data,
            "education_zh": self.education_zh.data,
            "education_en": self.education_en.data,
            "entry_year": self.entry_year.data,
            "graduation_year": self.graduation_year.data,
            "research_focus_zh": self.research_focus_zh.data,
            "research_focus_en": self.research_focus_en.data,
            "thesis_title_zh": self.thesis_title_zh.data,
            "thesis_title_en": self.thesis_title_en.data,
            "skills": self.skills.data,
            "bio_zh": self.bio_zh.data,
            "bio_en": self.bio_en.data,
            "current_affiliation": self.current_affiliation.data,
            "current_position": self.current_position.data,
            "destination_public": self.destination_public.data,
            "email_public": self.email_public.data,
            "orcid_url": self.orcid_url.data,
            "scholar_url": self.scholar_url.data,
            "github_url": self.github_url.data,
            "linkedin_url": self.linkedin_url.data,
            "external_url": self.external_url.data,
            "external_url_label": self.external_url_label.data,
            "photo_alt_zh": self.photo_alt_zh.data,
            "photo_alt_en": self.photo_alt_en.data,
            "sort_order": self.sort_order.data,
            "is_featured": self.is_featured.data,
        }

    def load_from(self, person) -> None:
        """把既有人物資料填入表單（編輯頁 GET）。

        skills 是 JSON 陣列，需轉回逗號分隔字串供 tag input 顯示。
        """
        self.name_zh.data = person.name_zh
        self.name_en.data = person.name_en
        self.slug.data = person.slug
        self.status.data = person.status
        self.title_zh.data = person.title_zh
        self.title_en.data = person.title_en
        self.degree.data = person.degree
        self.education_zh.data = person.education_zh
        self.education_en.data = person.education_en
        self.entry_year.data = person.entry_year
        self.graduation_year.data = person.graduation_year
        self.research_focus_zh.data = person.research_focus_zh
        self.research_focus_en.data = person.research_focus_en
        self.thesis_title_zh.data = person.thesis_title_zh
        self.thesis_title_en.data = person.thesis_title_en
        self.skills.data = ", ".join(person.skills)
        self.bio_zh.data = person.bio_zh
        self.bio_en.data = person.bio_en
        self.current_affiliation.data = person.current_affiliation
        self.current_position.data = person.current_position
        self.destination_public.data = person.destination_public
        self.email_public.data = person.email_public
        self.orcid_url.data = person.orcid_url
        self.scholar_url.data = person.scholar_url
        self.github_url.data = person.github_url
        self.linkedin_url.data = person.linkedin_url
        self.external_url.data = person.external_url
        self.external_url_label.data = person.external_url_label
        self.photo_alt_zh.data = person.photo_alt_zh
        self.photo_alt_en.data = person.photo_alt_en
        self.sort_order.data = person.sort_order
        self.is_featured.data = person.is_featured


#: GraduateForm 的欄位前綴。
#:
#: 為什麼需要：GraduateForm 的 graduation_year / degree /
#: thesis_title_zh 與 PersonForm 同名，而兩份表單渲染在同一頁上。
#: WTForms 以欄位名產生 id，因此頁面會出現重複的 id ——
#: 那不只是 HTML 無效，更直接的後果是「轉為畢業生」區塊裡的
#: <label for="degree"> 會指到上方主表單的欄位，
#: 點標籤時游標跳到錯誤的輸入框。
#:
#: 加上 prefix 後 name 與 id 都變成 graduate-*，衝突消失。
#: 注意：render 與讀取 POST 兩邊都必須帶同一個 prefix，
#: 否則會變成「填了但讀不到」。
GRADUATE_FORM_PREFIX = "graduate"


class GraduateForm(AdminForm):
    """在學轉畢業表單（SAI §7.5 的確認視窗）。

    實例化時必須帶 prefix=GRADUATE_FORM_PREFIX（見該常數說明）。
    """

    graduation_year = IntegerField(
        "畢業年度 *",
        validators=[DataRequired(message="請填寫畢業年度。"), NumberRange(min=1900, max=2200)],
    )
    degree = StringField("學位", validators=[Optional(), Length(max=80)])
    thesis_title_zh = TextAreaField(
        "論文題目",
        validators=[Optional()],
        description="留空則沿用既有論文題目（SAI §7.5）。",
    )
    submit = SubmitField("確認轉為畢業生")


class ResearchForm(AdminForm):
    """研究成果新增/編輯表單（SAI §15.2）。"""

    # --- Identity ---
    output_type = SelectField(
        "成果類型 *",
        choices=[(t, OutputType.LABELS_ZH[t]) for t in OutputType.ALL],
        validators=[DataRequired()],
    )
    year = IntegerField(
        "年份 *",
        validators=[DataRequired(message="年份為必填。"), NumberRange(min=1900, max=2200)],
    )
    publication_date = StringField(
        "出版日期",
        validators=[Optional(), Length(max=10), IsoDate()],
        description="格式 YYYY-MM-DD，有正式日期時填寫。",
        render_kw={"placeholder": "2025-03-14"},
    )
    title_zh = TextAreaField("標題（中）", validators=[Optional()])
    title_en = TextAreaField("標題（英）", validators=[Optional()])
    slug = StringField(
        "網址 slug",
        validators=[Optional(), Length(max=150)],
        description="留空時由標題自動產生。發布後變更會自動建立 301 轉址。",
    )

    # --- Summary & body（SAI §5.3 四段式）---
    summary_zh = TextAreaField(
        "摘要（中）",
        validators=[Optional()],
        description="約 80-180 字，說清楚 Problem / Method / Result / Significance。",
    )
    summary_en = TextAreaField("摘要（英）", validators=[Optional()])
    problem_zh = TextAreaField("研究問題 Problem", validators=[Optional()])
    method_zh = TextAreaField("研究方法 Method", validators=[Optional()])
    results_zh = TextAreaField("主要結果 Key results", validators=[Optional()])
    significance_zh = TextAreaField("貢獻與限制 Significance", validators=[Optional()])

    # --- Publication ---
    venue = StringField("期刊／會議／平台", validators=[Optional(), Length(max=255)])
    doi = StringField(
        "DOI",
        validators=[Optional(), Length(max=255), NormalisableDoi()],
        description="可貼裸值或完整網址，系統會自動正規化。",
    )
    external_url = StringField(
        "出版社／專案頁", validators=[Optional(), Length(max=500), SafeUrl()]
    )
    github_url = StringField(
        "程式碼 Repository", validators=[Optional(), Length(max=500), SafeUrl()]
    )
    dataset_url = StringField(
        "資料集連結", validators=[Optional(), Length(max=500), SafeUrl()]
    )
    authors_display_text = TextAreaField(
        "完整作者列",
        validators=[Optional()],
        description="含非 Lab 共同作者的正式作者列；Lab 成員另於下方關聯。",
    )

    # --- People & keywords ---
    #
    # 關聯成員不再使用 SelectMultipleField（見 parse_author_orders）。
    # 由 route 解析 author_order-<person_id> 後注入 to_dict 的結果。
    keywords = StringField(
        "研究關鍵字", validators=[Optional()], description="以逗號分隔，中英文皆可。"
    )

    # --- Media ---
    hero_image = FileField(
        "主圖",
        validators=[Optional(), FileAllowed(_IMAGE_EXTENSIONS, _IMAGE_MESSAGE)],
        render_kw={"accept": _IMAGE_ACCEPT},
    )
    hero_image_alt_zh = StringField(
        "主圖替代文字（中）",
        validators=[Optional(), Length(max=220)],
        description="有主圖時為必填（SAI §16、AC-12）。",
    )
    hero_image_alt_en = StringField(
        "主圖替代文字（英）",
        validators=[Optional(), Length(max=220)],
        description="選填。留空時英文版會沿用中文版並標記為中文（WCAG 3.1.2）。",
    )

    # --- SEO & ordering ---
    # seo_title_zh / seo_description_zh 已從表單移除（理由同 PersonForm）。
    sort_order = IntegerField("排序值", validators=[Optional(), NumberRange(min=0, max=99999)])

    submit = SubmitField("儲存")

    def to_dict(self) -> dict:
        """轉為 ResearchService 可接受的欄位 dict。"""
        return {
            "output_type": self.output_type.data,
            "year": self.year.data,
            "publication_date": self.publication_date.data,
            "title_zh": self.title_zh.data,
            "title_en": self.title_en.data,
            "slug": self.slug.data,
            "summary_zh": self.summary_zh.data,
            "summary_en": self.summary_en.data,
            "problem_zh": self.problem_zh.data,
            "method_zh": self.method_zh.data,
            "results_zh": self.results_zh.data,
            "significance_zh": self.significance_zh.data,
            "venue": self.venue.data,
            "doi": self.doi.data,
            "external_url": self.external_url.data,
            "github_url": self.github_url.data,
            "dataset_url": self.dataset_url.data,
            "authors_display_text": self.authors_display_text.data,
            # people 不在此產生：作者順序由 route 以
            # parse_author_orders() 解析後注入（見該方法的說明）。
            "keywords": self.keywords.data,
            "hero_image_alt_zh": self.hero_image_alt_zh.data,
            "hero_image_alt_en": self.hero_image_alt_en.data,
            "sort_order": self.sort_order.data,
        }

    @staticmethod
    def parse_author_orders(form_data, valid_person_ids) -> tuple[list[dict], list[str]]:
        """解析每位成員的作者順序輸入。

        表單命名為 `author_order-<person_id>`：留空代表不列入，
        填數字代表列入並以該數字排序（1 = 第一作者）。

        為什麼不用 SelectMultipleField（原本的做法）：
          多選清單送出的順序是「選項在 DOM 中的順序」，也就是
          _person_choices() 的排列（人物的 sort_order），
          與管理者點選的順序無關。而 sync_people 以清單順序
          寫入 sort_order，前台的 public_lab_people 又照它顯示 ——
          結果是「論文的作者順序 = 這些人在成員頁的排序值」，
          且在成果頁完全無法調整。對學術網站來說，
          第一作者與通訊作者的順序是不能錯的。

        為什麼以 person_id 為 key 而非依索引對齊：
          parse_repeated 那種依索引對齊的做法有個隱性前提 ——
          每一列的每個欄位都必須送出。以 id 為 key 完全不受
          「某個輸入沒送出」影響，不會發生 A 的順序配到 B 身上。

        Returns:
            (entries, errors)
            entries 已依順序排好，可直接交給 ResearchService.sync_people。
        """
        entries: list[dict] = []
        errors: list[str] = []

        for person_id in valid_person_ids:
            raw = (form_data.get(f"author_order-{person_id}") or "").strip()
            if not raw:
                continue  # 留空 = 不列入這筆成果

            try:
                order = int(raw)
            except ValueError:
                errors.append(f"成員順序「{raw}」不是數字，請填 1 以上的整數。")
                continue

            if order < 1:
                errors.append(f"成員順序「{raw}」必須是 1 以上的整數。")
                continue

            entries.append(
                {
                    "person_id": person_id,
                    "role": ContributorRole.AUTHOR,
                    "author_order": order,
                }
            )

        # 相同順序值時以 person_id 決定先後，確保結果穩定可預期。
        entries.sort(key=lambda e: (e["author_order"], e["person_id"]))

        orders = [e["author_order"] for e in entries]
        if len(set(orders)) != len(orders):
            errors.append(
                "有多位成員填了相同的順序值，系統已依成員編號決定先後；"
                "建議改為不重複的數字以免順序不如預期。"
            )

        return entries, errors

    def load_from(self, output) -> None:
        """把既有成果資料填入表單（編輯頁 GET）。"""
        self.output_type.data = output.output_type
        self.year.data = output.year
        self.publication_date.data = (
            output.publication_date.isoformat() if output.publication_date else ""
        )
        self.title_zh.data = output.title_zh
        self.title_en.data = output.title_en
        self.slug.data = output.slug
        self.summary_zh.data = output.summary_zh
        self.summary_en.data = output.summary_en
        self.problem_zh.data = output.problem_zh
        self.method_zh.data = output.method_zh
        self.results_zh.data = output.results_zh
        self.significance_zh.data = output.significance_zh
        self.venue.data = output.venue
        self.doi.data = output.doi
        self.external_url.data = output.external_url
        self.github_url.data = output.github_url
        self.dataset_url.data = output.dataset_url
        self.authors_display_text.data = output.authors_display_text
        # 作者順序不經由 form 欄位，由 template 直接讀 output.person_links。
        self.keywords.data = ", ".join(output.keywords)
        self.hero_image_alt_zh.data = output.hero_image_alt_zh
        self.hero_image_alt_en.data = output.hero_image_alt_en
        self.sort_order.data = output.sort_order


class SiteSettingForm(AdminForm):
    """網站設定表單（SAI §15.4 的六個 tab）。

    研究主題 / 可驗證事實 / 外部連結三組可變長度資料
    不在此宣告欄位，由 route 以 parse_repeated() 解析
    （見檔頭「特殊機制（多值欄位）」）。
    """

    # --- Lab Identity ---
    lab_name_zh = StringField(
        "研究室名稱（中）*", validators=[DataRequired(), Length(max=160)]
    )
    lab_name_en = StringField(
        "研究室名稱（英）*", validators=[DataRequired(), Length(max=200)]
    )
    # short_name 已從表單移除：全專案沒有任何地方讀取它
    # （公開模板、SEO、結構化資料皆使用 lab_name_zh / lab_name_en）。
    # 保留 DB 欄位與既有資料。
    department_zh = StringField("系所（中）", validators=[Optional(), Length(max=160)])
    department_en = StringField("系所（英）", validators=[Optional(), Length(max=200)])
    university_zh = StringField("學校（中）", validators=[Optional(), Length(max=160)])
    university_en = StringField("學校（英）", validators=[Optional(), Length(max=200)])
    logo = FileField(
        "Logo",
        validators=[Optional(), FileAllowed(_IMAGE_EXTENSIONS, _IMAGE_MESSAGE)],
        render_kw={"accept": _IMAGE_ACCEPT},
    )

    # --- Homepage ---
    hero_title_zh = TextAreaField("首頁主標（中）", validators=[Optional()])
    hero_title_en = TextAreaField("首頁主標（英）", validators=[Optional()])
    hero_intro_zh = TextAreaField("首頁導言（中）", validators=[Optional()])
    hero_intro_en = TextAreaField("首頁導言（英）", validators=[Optional()])
    hero_media = FileField(
        "首頁主視覺",
        validators=[Optional(), FileAllowed(_IMAGE_EXTENSIONS, _IMAGE_MESSAGE)],
        render_kw={"accept": _IMAGE_ACCEPT},
    )
    hero_media_alt_zh = StringField(
        "首頁主視覺替代文字", validators=[Optional(), Length(max=220)]
    )

    # --- About ---
    about_intro_zh = TextAreaField("關於研究室內容", validators=[Optional()])
    about_intro_en = TextAreaField("關於研究室內容（英）", validators=[Optional()])
    about_methods_zh = TextAreaField("研究方法與設備概覽", validators=[Optional()])

    # --- Join & Contact ---
    #: 這些欄位在 service 層都會經過 normalize_email / normalize_url，
    #: 非法值會被轉成 None 而「覆寫掉原本正確的資料」。
    #: 因此表單層必須先擋下來（理由同 SafeUrl 的 docstring）——
    #: 少了 SafeEmail，打錯一個字就會把研究室對外的聯絡信箱清空，
    #: 而畫面仍顯示「已更新網站設定」。
    contact_email = StringField(
        "聯絡 Email",
        validators=[Optional(), Length(max=200), SafeEmail()],
        render_kw={"type": "email"},
    )
    address_zh = TextAreaField("地址（中）", validators=[Optional()])
    address_en = TextAreaField("地址（英）", validators=[Optional()])
    map_url = StringField("地圖連結", validators=[Optional(), Length(max=500), SafeUrl()])
    join_title_zh = StringField("招募標題", validators=[Optional(), Length(max=200)])
    join_body_zh = TextAreaField("招募內容", validators=[Optional()])
    join_cta_label_zh = StringField("招募按鈕文字", validators=[Optional(), Length(max=120)])
    join_cta_url = StringField(
        "招募按鈕連結", validators=[Optional(), Length(max=500), SafeUrl()]
    )

    # --- SEO defaults ---
    default_title_suffix = StringField(
        "標題後綴", validators=[Optional(), Length(max=120)],
        description="例如「NTUST SiPh Lab」，會附加在各頁標題之後。",
    )
    default_description_zh = TextAreaField(
        "預設描述", validators=[Optional(), Length(max=320)]
    )
    og_image = FileField(
        "預設社群分享圖",
        validators=[Optional(), FileAllowed(_IMAGE_EXTENSIONS, _IMAGE_MESSAGE)],
        render_kw={"accept": _IMAGE_ACCEPT},
    )
    # production_base_url 已從表單移除：它的說明本來就寫著
    # 「僅供紀錄；實際 canonical 由環境變數 PUBLIC_BASE_URL 決定」——
    # 也就是填了不會有任何效果，卻讓管理者以為改了它就會改網域。
    # 保留 DB 欄位與既有資料。

    # --- External identity ---
    official_ntust_url = StringField(
        "NTUST 官方頁連結", validators=[Optional(), Length(max=500), SafeUrl()]
    )

    # --- Advanced ---
    # llms_txt_enabled 已從表單移除。
    #
    # 這個開關實際上要兩個條件同時成立才生效：這裡打勾，
    # 而且伺服器端要設 ENABLE_LLMS_TXT=true。也就是說在後台
    # 勾了它，多數情況下什麼事都不會發生 —— 而欄位說明本身就寫著
    # 「並非 Google 排名的必要條件，也不保證任何排名效果」。
    #
    # 一個「勾了通常沒作用、而且官方說明不需要」的開關，
    # 放在後台只會讓管理者困惑。需要啟用時改環境變數即可，
    # 那本來就是部署層的決定（SAI §13.2）。
    # DB 欄位與既有值保留，SettingsService 仍接受明確傳入。

    submit = SubmitField("儲存設定")

    def to_dict(self) -> dict:
        """轉為 SettingsService 可接受的欄位 dict（不含多值區塊與檔案）。"""
        return {
            "lab_name_zh": self.lab_name_zh.data,
            "lab_name_en": self.lab_name_en.data,
            "department_zh": self.department_zh.data,
            "department_en": self.department_en.data,
            "university_zh": self.university_zh.data,
            "university_en": self.university_en.data,
            "hero_title_zh": self.hero_title_zh.data,
            "hero_title_en": self.hero_title_en.data,
            "hero_intro_zh": self.hero_intro_zh.data,
            "hero_intro_en": self.hero_intro_en.data,
            "hero_media_alt_zh": self.hero_media_alt_zh.data,
            "about_intro_zh": self.about_intro_zh.data,
            "about_intro_en": self.about_intro_en.data,
            "about_methods_zh": self.about_methods_zh.data,
            "contact_email": self.contact_email.data,
            "address_zh": self.address_zh.data,
            "address_en": self.address_en.data,
            "map_url": self.map_url.data,
            "join_title_zh": self.join_title_zh.data,
            "join_body_zh": self.join_body_zh.data,
            "join_cta_label_zh": self.join_cta_label_zh.data,
            "join_cta_url": self.join_cta_url.data,
            "default_title_suffix": self.default_title_suffix.data,
            "default_description_zh": self.default_description_zh.data,
            "official_ntust_url": self.official_ntust_url.data,
        }

    def load_from(self, setting) -> None:
        """把既有設定填入表單。"""
        self.lab_name_zh.data = setting.lab_name_zh
        self.lab_name_en.data = setting.lab_name_en
        self.department_zh.data = setting.department_zh
        self.department_en.data = setting.department_en
        self.university_zh.data = setting.university_zh
        self.university_en.data = setting.university_en
        self.hero_title_zh.data = setting.hero_title_zh
        self.hero_title_en.data = setting.hero_title_en
        self.hero_intro_zh.data = setting.hero_intro_zh
        self.hero_intro_en.data = setting.hero_intro_en
        self.hero_media_alt_zh.data = setting.hero_media_alt_zh
        self.about_intro_zh.data = setting.about_intro_zh
        self.about_intro_en.data = setting.about_intro_en
        self.about_methods_zh.data = setting.about_methods_zh
        self.contact_email.data = setting.contact_email
        self.address_zh.data = setting.address_zh
        self.address_en.data = setting.address_en
        self.map_url.data = setting.map_url
        self.join_title_zh.data = setting.join_title_zh
        self.join_body_zh.data = setting.join_body_zh
        self.join_cta_label_zh.data = setting.join_cta_label_zh
        self.join_cta_url.data = setting.join_cta_url
        self.default_title_suffix.data = setting.default_title_suffix
        self.default_description_zh.data = setting.default_description_zh
        self.official_ntust_url.data = setting.official_ntust_url

    @staticmethod
    def parse_repeated(form_data, keys: list[str], prefix: str) -> list[dict]:
        """解析可變長度的重複欄位群組。

        表單命名慣例：`<prefix>-<key>` 的多值欄位，
        例如 research_focus-title_zh、research_focus-title_en。
        同一索引位置的值組成一筆記錄。

        Args:
            form_data: request.form（MultiDict）。
            keys: 每筆記錄的欄位名稱清單。
            prefix: 欄位前綴。

        Returns:
            list[dict]，長度為最長那一組的長度；
            缺值以空字串補齊，由 service 的 _clean_* 過濾。

        為什麼用索引對齊而非 JSON：
          純 HTML 表單新增列時不需要 JS 就能運作
          （SAI §6.3 禁止「hover 才出現唯一操作」的精神延伸：
          功能不應該完全依賴 JS）。

        ★ 索引對齊的隱性前提（務必遵守）：
          「每一列的每個欄位都必須送出，即使是空值。」
          text/url 這類 input 空值也會送出，因此成立。

          但 checkbox 未勾選時「完全不會出現在 POST 裡」。
          只要有人在 repeat row 加一個 checkbox，該欄位的
          list 就會比其他欄位短，從那一列開始所有欄位橫向錯位 ——
          A 的網址會配到 B 的名稱上，而且不會有任何錯誤，
          資料就這樣靜靜地錯了。

          因此在下方明確檢查各欄位長度是否一致，不一致就拋錯，
          讓問題在開發期就爆出來而不是變成髒資料。
          若真的需要 checkbox，請改用 hidden + checkbox 配對送值，
          或改以 id 為 key（見 ResearchForm.parse_author_orders）。

        Raises:
            ValueError: 各欄位送出的數量不一致（見上）。
        """
        columns = {key: form_data.getlist(f"{prefix}-{key}") for key in keys}

        lengths = {key: len(values) for key, values in columns.items()}
        if len(set(lengths.values())) > 1:
            raise ValueError(
                f"{prefix} 的重複欄位長度不一致：{lengths}。"
                "repeat row 內不得使用 checkbox 或任何『未選取就不送出』的控制項，"
                "否則欄位會橫向錯位（見 parse_repeated 的說明）。"
            )

        length = max((len(values) for values in columns.values()), default=0)

        records: list[dict] = []
        for index in range(length):
            record = {}
            for key in keys:
                values = columns[key]
                record[key] = values[index] if index < len(values) else ""
            records.append(record)
        return records


class ChangePasswordForm(AdminForm):
    """修改管理員密碼（SAI §7.3 System 選單）。

    要求輸入舊密碼：這是「已登入使用者主動修改」的情境，
    與 CLI reset-password（忘記密碼、具伺服器權限）不同。
    要求舊密碼可防止「攻擊者取得未鎖定的瀏覽器 session 後
    直接改密碼奪取帳號」。
    """

    current_password = PasswordField(
        "目前密碼 *", validators=[DataRequired(message="請輸入目前密碼。")]
    )
    new_password = PasswordField(
        "新密碼 *",
        validators=[
            DataRequired(message="請輸入新密碼。"),
            Length(min=12, message="密碼長度至少 12 字元（SAI §11.2）。"),
        ],
    )
    confirm_password = PasswordField(
        "確認新密碼 *",
        validators=[
            DataRequired(message="請再次輸入新密碼。"),
            EqualTo("new_password", message="兩次輸入的新密碼不一致。"),
        ],
    )
    submit = SubmitField("更新密碼")
