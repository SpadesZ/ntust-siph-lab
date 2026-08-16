# ============================================================
# NTUST SiPh Lab - Admin CMS Blueprint (/admin)
#
# 上下游：
#   已登入管理員 -> admin blueprint（全域 @login_required）
#       -> forms（驗證 + CSRF）
#       -> PersonService / ResearchService / SettingsService
#       -> Redirect / AuditLog
#       -> PRG（Post/Redirect/Get）-> flash -> 列表或詳細頁
#   Dashboard -> repositories（統計、needs attention）
#            -> HealthService（System health）
#
# 檔案路徑：
#   app/blueprints/admin/routes.py
#
# 建立日期：2026-08-14
# 最後重大修改：2026-08-14
# 版本：v1.0
#
# 模組定位與責任邊界：
#   SAI §7 定義的單一管理員 CMS。實作附錄 A Route Matrix 中
#   所有 Admin 路由，以及 §7.2 Dashboard、§7.3 側選單、
#   §7.5 畢業流程、§7.6 刪除政策。
#
#   責任邊界（SAI §9.1，不得做的事）：
#     - 不得把複雜 DB 邏輯寫在 route（一律呼叫 service）。
#     - 不得直接 commit（交易邊界在 service）。
#     - 不得繞過 PublishValidator 直接改 publish_status。
#
# 輸入 -> 處理 -> 輸出 Pipeline（SAI §9.3）：
#   POST /admin/research/<id>/edit
#     -> @login_required
#     -> CSRF validation（Flask-WTF）
#     -> form validation
#     -> ResearchService.update(...)
#          -> sanitize / normalize -> DB transaction
#          -> slug 變更則建立 Redirect -> 寫 AuditLog
#     -> PRG: redirect to GET list
#     -> flash success
#
# 主要 Function：
#   dashboard()
#   people_list / person_new / person_edit / person_publish /
#   person_unpublish / person_archive / person_graduate /
#   person_photo_delete
#   research_list / research_new / research_edit / research_publish /
#   research_unpublish / research_archive / research_feature /
#   research_image_delete
#   alumni_list
#   settings() / settings_media_delete()
#   system() / change_password()
#
# 依賴套件：
#   flask, flask-login, app.services, app.repositories, .forms
#
# 環境變數：無直接使用。
#
# 資料庫使用方式：
#   透過 repositories 讀、透過 services 寫。本檔不呼叫 commit。
#
# Error Handling / Fallback：
#   - Service 拋出的業務例外（PersonServiceError 等）以 flash
#     顯示並重新 render 表單，不讓使用者看到 500。
#   - MediaError（上傳問題）同樣以 flash 呈現在表單旁。
#   - 找不到資源 -> abort(404)。
#
# 特殊機制（PRG 模式）：
#   所有成功的 POST 都以 redirect 結束（SAI §9.3 明列 PRG）。
#   為什麼：避免使用者按重新整理時重複送出表單，
#   造成重複建立內容或重複觸發狀態轉換。
#
# 特殊機制（全域 login_required）：
#   使用 before_request 對整個 blueprint 套用，而非逐個 route
#   加裝飾器。為什麼：漏加裝飾器是最常見的權限漏洞，
#   而「預設全部要登入」讓遺漏不可能發生（AC-01）。
#
# 已知限制與禁止事項：
#   1. 不提供一鍵永久刪除（SAI §7.6：預設不硬刪，先 archive）。
#   2. 不提供新增第二個管理員的 UI（ADR-007）。
#   3. 禁止在此顯示或記錄密碼。
#
# 維護契約：
#   1. 新增任何 state-changing route 必須是 POST 且有 CSRF token。
#   2. 新增任何 route 都自動受 before_request 保護；
#      若某 route 需要匿名存取，必須明確放在 auth blueprint。
#
# 驗證方式：
#   pytest tests/test_admin.py tests/test_auth.py
# ============================================================

from __future__ import annotations

import logging

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from app.extensions import db
from app.models.audit_log import AuditLog
from app.models.mixins import AuditAction, OutputType, PersonStatus, PublishStatus
from app.models.person import Person
from app.models.research_output import ResearchOutput
from app.repositories import people as people_repo
from app.repositories import research as research_repo
from app.repositories.settings import (
    count_redirects,
    get_site_settings,
    recent_changes,
    recent_security_events,
)
from app.services.health_service import HealthService
from app.services.media_service import MediaError
from app.services.person_service import PersonService, PersonServiceError
from app.services.publish_validator import PublishValidator
from app.services.research_service import ResearchService, ResearchServiceError
from app.services.settings_service import SettingsService, SettingsServiceError
from app.blueprints.admin.forms import (
    ChangePasswordForm,
    ConfirmForm,
    GraduateForm,
    PersonForm,
    ResearchForm,
    SiteSettingForm,
)

logger = logging.getLogger(__name__)

admin_bp = Blueprint("admin", __name__, template_folder="../../templates")


# ----------------------------------------------------------------------
# 全域權限保護
# ----------------------------------------------------------------------
@admin_bp.before_request
@login_required
def require_login():
    """整個 admin blueprint 都需要登入（AC-01）。

    見檔頭「特殊機制（全域 login_required）」。
    未登入時 Flask-Login 會導向 login_view（/admin/login）
    並帶上 ?next=，登入後回到原本目標頁。
    """
    return None


def _actor() -> tuple[int | None, str | None]:
    """取得當前操作者 id 與來源 IP，供 AuditLog 使用。"""
    return (
        current_user.id if current_user.is_authenticated else None,
        request.remote_addr,
    )


# ----------------------------------------------------------------------
# Dashboard
# ----------------------------------------------------------------------
@admin_bp.route("/", strict_slashes=False)
def dashboard():
    """管理首頁（SAI §7.2 的四個區塊 + System health）。

    strict_slashes=False 的理由：
      SAI §7.1 與 AC-01 規定「未登入進 /admin -> /admin/login」，
      指定的路徑是不含尾斜線的 /admin。Flask 預設會對
      /admin 回 308 轉址到 /admin/，多一次往返且讓
      AC-01 的驗收出現非預期的中間狀態。關閉 strict_slashes
      讓 /admin 與 /admin/ 都直接命中此 route。
    """
    person_counts = people_repo.count_by_status()
    research_counts = research_repo.count_by_status()

    issues = people_repo.needs_attention() + research_repo.needs_attention()
    # blocking 排前面，讓真正會擋發布的問題先被看到。
    issues.sort(key=lambda i: {"blocking": 0, "warning": 1, "info": 2}.get(i["severity"], 3))

    return render_template(
        "admin/dashboard.html",
        person_counts=person_counts,
        research_counts=research_counts,
        issues=issues,
        recent=recent_changes(limit=10),
        health=HealthService.system_report(),
        redirect_count=count_redirects(),
        legacy_pending=people_repo.count_legacy_pending(),
    )


# ----------------------------------------------------------------------
# 人物
# ----------------------------------------------------------------------
@admin_bp.route("/people")
def people_list():
    """人物列表（SAI §7.3：列表 / 搜尋 / 排序）。"""
    status = request.args.get("status") or None
    publish_status = request.args.get("publish_status") or None
    query = (request.args.get("q") or "").strip() or None

    return render_template(
        "admin/people_list.html",
        people=people_repo.admin_list(
            status=status, publish_status=publish_status, query=query
        ),
        active_status=status,
        active_publish_status=publish_status,
        active_query=query,
        person_statuses=PersonStatus.ALL,
        publish_statuses=PublishStatus.ALL,
        confirm_form=ConfirmForm(),
    )


def _handle_person_photo(person: Person, form: PersonForm) -> None:
    """若表單附帶照片檔則上傳並綁定。

    抽成函式的理由：新增與編輯兩條路徑都需要，
    且錯誤處理方式必須完全一致（否則會出現
    「新增時上傳失敗整筆失敗、編輯時卻靜默略過」的不一致）。
    """
    uploaded = form.photo.data
    if not uploaded or not getattr(uploaded, "filename", ""):
        return

    admin_id, ip = _actor()
    PersonService.attach_photo(
        person,
        uploaded,
        alt_zh=form.photo_alt_zh.data,
        admin_user_id=admin_id,
        ip_address=ip,
    )


@admin_bp.route("/people/new", methods=["GET", "POST"])
def person_new():
    """新增人物（SAI 附錄 A：GET/POST /admin/people/new）。"""
    form = PersonForm()

    if form.validate_on_submit():
        admin_id, ip = _actor()
        try:
            person = PersonService.create(form.to_dict(), admin_user_id=admin_id, ip_address=ip)
        except PersonServiceError as exc:
            flash(str(exc), "error")
            return render_template("admin/person_form.html", form=form, person=None)

        # 到這裡人物已經建立並 commit。照片是獨立的後續步驟，
        # 其失敗「不得」把使用者送回新增表單 —— 那會讓管理員以為
        # 整筆都失敗而重新送出，結果建立第二筆人物
        # （slug 會被自動去重成 -2，產生難以察覺的重複實體）。
        # 改為導向已建立人物的編輯頁，並明確說明哪一半成功了。
        try:
            _handle_person_photo(person, form)
        except (PersonServiceError, MediaError) as exc:
            flash(
                f"「{person.name_zh}」已建立（草稿），但照片未能上傳：{exc} "
                "請在本頁重新上傳照片，不要重複新增人物。",
                "error",
            )
            return redirect(url_for("admin.person_edit", person_id=person.id))

        flash(f"已建立「{person.name_zh}」（草稿）。請確認內容後再發布。", "success")
        return redirect(url_for("admin.person_edit", person_id=person.id))

    return render_template("admin/person_form.html", form=form, person=None)


@admin_bp.route("/people/<int:person_id>/edit", methods=["GET", "POST"])
def person_edit(person_id: int):
    """編輯人物（SAI 附錄 A：GET/POST /admin/people/<id>/edit）。"""
    person = db.session.get(Person, person_id)
    if person is None:
        abort(404)

    form = PersonForm()

    if form.validate_on_submit():
        admin_id, ip = _actor()
        try:
            PersonService.update(person, form.to_dict(), admin_user_id=admin_id, ip_address=ip)
            _handle_person_photo(person, form)
        except (PersonServiceError, MediaError) as exc:
            flash(str(exc), "error")
            return render_template(
                "admin/person_form.html",
                form=form,
                person=person,
                validation=PublishValidator.validate_person(person),
                graduate_form=GraduateForm(),
                confirm_form=ConfirmForm(),
            )

        flash(f"已更新「{person.name_zh}」。", "success")
        return redirect(url_for("admin.person_edit", person_id=person.id))

    if request.method == "GET":
        form.load_from(person)

    return render_template(
        "admin/person_form.html",
        form=form,
        person=person,
        validation=PublishValidator.validate_person(person),
        graduate_form=GraduateForm(),
        confirm_form=ConfirmForm(),
    )


@admin_bp.route("/people/<int:person_id>/publish", methods=["POST"])
def person_publish(person_id: int):
    """發布人物（AC-04）。未通過門檻時以 flash 顯示所有原因。"""
    person = db.session.get(Person, person_id)
    if person is None:
        abort(404)

    if not ConfirmForm().validate_on_submit():
        abort(400)

    admin_id, ip = _actor()
    try:
        PersonService.publish(person, admin_user_id=admin_id, ip_address=ip)
        flash(f"已發布「{person.name_zh}」，現在會出現在前台。", "success")
    except PersonServiceError as exc:
        flash(str(exc), "error")

    return redirect(url_for("admin.person_edit", person_id=person_id))


@admin_bp.route("/people/<int:person_id>/unpublish", methods=["POST"])
def person_unpublish(person_id: int):
    """取消發布（退回草稿）。"""
    person = db.session.get(Person, person_id)
    if person is None:
        abort(404)
    if not ConfirmForm().validate_on_submit():
        abort(400)

    admin_id, ip = _actor()
    PersonService.unpublish(person, admin_user_id=admin_id, ip_address=ip)
    flash(f"已將「{person.name_zh}」退回草稿，前台不再顯示。", "success")
    return redirect(url_for("admin.person_edit", person_id=person_id))


@admin_bp.route("/people/<int:person_id>/archive", methods=["POST"])
def person_archive(person_id: int):
    """封存人物（SAI §7.6：預設不硬刪）。"""
    person = db.session.get(Person, person_id)
    if person is None:
        abort(404)
    if not ConfirmForm().validate_on_submit():
        abort(400)

    admin_id, ip = _actor()
    PersonService.archive(person, admin_user_id=admin_id, ip_address=ip)
    flash(f"已封存「{person.name_zh}」。資料仍保留，可隨時重新發布。", "success")
    return redirect(url_for("admin.person_edit", person_id=person_id))


@admin_bp.route("/people/<int:person_id>/graduate", methods=["POST"])
def person_graduate(person_id: int):
    """在學轉畢業（SAI §7.5、AC-06）。

    保證 person id 與 slug 不變，既有成果關聯完全保留。
    """
    person = db.session.get(Person, person_id)
    if person is None:
        abort(404)

    form = GraduateForm()
    if not form.validate_on_submit():
        for field_errors in form.errors.values():
            for message in field_errors:
                flash(message, "error")
        return redirect(url_for("admin.person_edit", person_id=person_id))

    admin_id, ip = _actor()
    try:
        PersonService.graduate(
            person,
            graduation_year=form.graduation_year.data,
            degree=form.degree.data,
            thesis_title_zh=form.thesis_title_zh.data,
            admin_user_id=admin_id,
            ip_address=ip,
        )
        flash(
            f"已將「{person.name_zh}」轉為畢業生。個人網址與研究成果關聯皆未變動。",
            "success",
        )
    except PersonServiceError as exc:
        flash(str(exc), "error")

    return redirect(url_for("admin.person_edit", person_id=person_id))


@admin_bp.route("/people/<int:person_id>/photo/delete", methods=["POST"])
def person_photo_delete(person_id: int):
    """移除人物照片（SAI §16：先解除引用再刪檔）。"""
    person = db.session.get(Person, person_id)
    if person is None:
        abort(404)
    if not ConfirmForm().validate_on_submit():
        abort(400)

    admin_id, ip = _actor()
    PersonService.remove_photo(person, admin_user_id=admin_id, ip_address=ip)
    flash("已移除照片。", "success")
    return redirect(url_for("admin.person_edit", person_id=person_id))


# ----------------------------------------------------------------------
# 畢業生
# ----------------------------------------------------------------------
@admin_bp.route("/alumni")
def alumni_list():
    """畢業生年度列表（SAI §7.3）。"""
    return render_template(
        "admin/alumni_list.html",
        alumni_groups=people_repo.list_alumni_by_year(published_only=False),
        confirm_form=ConfirmForm(),
    )


# ----------------------------------------------------------------------
# 研究成果
# ----------------------------------------------------------------------
def _person_choices() -> list[tuple[int, str]]:
    """成果表單的人物多選選項。

    包含所有狀態的人物（含 draft），因為成果可能在人物尚未
    發布時就先建立關聯 —— 前台會自動過濾未發布者
    （見 ResearchOutput.public_lab_people）。
    """
    people = people_repo.admin_list()
    return [
        (
            person.id,
            f"{person.name_zh}"
            f"{f'（{person.name_en}）' if person.name_en else ''}"
            f" - {person.status}",
        )
        for person in people
    ]


def _handle_research_image(output: ResearchOutput, form: ResearchForm) -> None:
    """若表單附帶主圖則上傳並綁定（理由同 _handle_person_photo）。"""
    uploaded = form.hero_image.data
    if not uploaded or not getattr(uploaded, "filename", ""):
        return

    admin_id, ip = _actor()
    ResearchService.attach_hero_image(
        output,
        uploaded,
        alt_zh=form.hero_image_alt_zh.data,
        admin_user_id=admin_id,
        ip_address=ip,
    )


@admin_bp.route("/research")
def research_list():
    """成果列表（SAI §7.3：type / year / status 篩選）。"""
    from app.utils.validators import validate_year

    output_type = request.args.get("type") or None
    year = validate_year(request.args.get("year"))
    publish_status = request.args.get("publish_status") or None
    query = (request.args.get("q") or "").strip() or None

    return render_template(
        "admin/research_list.html",
        outputs=research_repo.admin_list(
            output_type=output_type, year=year, publish_status=publish_status, query=query
        ),
        available_years=research_repo.admin_available_years(),
        output_types=OutputType.ALL,
        type_labels=OutputType.LABELS_ZH,
        publish_statuses=PublishStatus.ALL,
        active_type=output_type,
        active_year=year,
        active_publish_status=publish_status,
        active_query=query,
        confirm_form=ConfirmForm(),
    )


@admin_bp.route("/research/new", methods=["GET", "POST"])
def research_new():
    """新增研究成果。"""
    form = ResearchForm()
    form.people.choices = _person_choices()

    if form.validate_on_submit():
        admin_id, ip = _actor()
        try:
            output = ResearchService.create(
                form.to_dict(), admin_user_id=admin_id, ip_address=ip
            )
        except ResearchServiceError as exc:
            flash(str(exc), "error")
            return render_template("admin/research_form.html", form=form, output=None)

        # 理由同 person_new：成果已 commit，主圖失敗不得回到新增表單，
        # 否則重送會建立重複成果。
        try:
            _handle_research_image(output, form)
        except (ResearchServiceError, MediaError) as exc:
            flash(
                f"「{output.display_title}」已建立（草稿），但主圖未能上傳：{exc} "
                "請在本頁重新上傳主圖，不要重複新增成果。",
                "error",
            )
            return redirect(url_for("admin.research_edit", output_id=output.id))

        flash(f"已建立「{output.display_title}」（草稿）。", "success")
        return redirect(url_for("admin.research_edit", output_id=output.id))

    return render_template("admin/research_form.html", form=form, output=None)


@admin_bp.route("/research/<int:output_id>/edit", methods=["GET", "POST"])
def research_edit(output_id: int):
    """編輯研究成果（slug 變更會自動建立 301，AC-10）。"""
    output = db.session.get(ResearchOutput, output_id)
    if output is None:
        abort(404)

    form = ResearchForm()
    form.people.choices = _person_choices()

    if form.validate_on_submit():
        admin_id, ip = _actor()
        try:
            ResearchService.update(
                output, form.to_dict(), admin_user_id=admin_id, ip_address=ip
            )
            _handle_research_image(output, form)
        except (ResearchServiceError, MediaError) as exc:
            flash(str(exc), "error")
            return render_template(
                "admin/research_form.html",
                form=form,
                output=output,
                validation=PublishValidator.validate_research(output),
                confirm_form=ConfirmForm(),
            )

        flash(f"已更新「{output.display_title}」。", "success")
        return redirect(url_for("admin.research_edit", output_id=output.id))

    if request.method == "GET":
        form.load_from(output)

    return render_template(
        "admin/research_form.html",
        form=form,
        output=output,
        validation=PublishValidator.validate_research(output),
        confirm_form=ConfirmForm(),
    )


@admin_bp.route("/research/<int:output_id>/publish", methods=["POST"])
def research_publish(output_id: int):
    """發布成果（AC-09）。"""
    output = db.session.get(ResearchOutput, output_id)
    if output is None:
        abort(404)
    if not ConfirmForm().validate_on_submit():
        abort(400)

    admin_id, ip = _actor()
    try:
        ResearchService.publish(output, admin_user_id=admin_id, ip_address=ip)
        flash(f"已發布「{output.display_title}」。", "success")
    except ResearchServiceError as exc:
        flash(str(exc), "error")

    return redirect(url_for("admin.research_edit", output_id=output_id))


@admin_bp.route("/research/<int:output_id>/unpublish", methods=["POST"])
def research_unpublish(output_id: int):
    """取消發布成果。"""
    output = db.session.get(ResearchOutput, output_id)
    if output is None:
        abort(404)
    if not ConfirmForm().validate_on_submit():
        abort(400)

    admin_id, ip = _actor()
    ResearchService.unpublish(output, admin_user_id=admin_id, ip_address=ip)
    flash(f"已將「{output.display_title}」退回草稿。", "success")
    return redirect(url_for("admin.research_edit", output_id=output_id))


@admin_bp.route("/research/<int:output_id>/archive", methods=["POST"])
def research_archive(output_id: int):
    """封存成果（SAI 附錄 A：POST /admin/research/<id>/archive）。"""
    output = db.session.get(ResearchOutput, output_id)
    if output is None:
        abort(404)
    if not ConfirmForm().validate_on_submit():
        abort(400)

    admin_id, ip = _actor()
    ResearchService.archive(output, admin_user_id=admin_id, ip_address=ip)
    flash(f"已封存「{output.display_title}」。", "success")
    return redirect(url_for("admin.research_edit", output_id=output_id))


@admin_bp.route("/research/<int:output_id>/feature", methods=["POST"])
def research_feature(output_id: int):
    """切換精選狀態（SAI §15.2：published 才能 featured）。"""
    output = db.session.get(ResearchOutput, output_id)
    if output is None:
        abort(404)
    if not ConfirmForm().validate_on_submit():
        abort(400)

    desired = request.form.get("featured") == "1"
    admin_id, ip = _actor()
    try:
        ResearchService.set_featured(output, desired, admin_user_id=admin_id, ip_address=ip)
        flash("已更新精選狀態。", "success")
    except ResearchServiceError as exc:
        flash(str(exc), "error")

    return redirect(url_for("admin.research_edit", output_id=output_id))


@admin_bp.route("/research/<int:output_id>/image/delete", methods=["POST"])
def research_image_delete(output_id: int):
    """移除成果主圖。"""
    output = db.session.get(ResearchOutput, output_id)
    if output is None:
        abort(404)
    if not ConfirmForm().validate_on_submit():
        abort(400)

    admin_id, ip = _actor()
    ResearchService.remove_hero_image(output, admin_user_id=admin_id, ip_address=ip)
    flash("已移除主圖。", "success")
    return redirect(url_for("admin.research_edit", output_id=output_id))


# ----------------------------------------------------------------------
# 網站設定
# ----------------------------------------------------------------------
@admin_bp.route("/settings", methods=["GET", "POST"])
def settings():
    """網站設定（SAI §15.4 六個 tab）。"""
    setting = get_site_settings()
    form = SiteSettingForm()

    if form.validate_on_submit():
        admin_id, ip = _actor()
        data = form.to_dict()

        # 多值區塊由 form 的 parse_repeated 解析（見 forms.py 說明）。
        data["research_focus"] = SiteSettingForm.parse_repeated(
            request.form, ["title_zh", "title_en", "description_zh"], "research_focus"
        )
        data["lab_proof"] = SiteSettingForm.parse_repeated(
            request.form, ["label_zh", "value_zh", "source"], "lab_proof"
        )
        data["social_links"] = SiteSettingForm.parse_repeated(
            request.form, ["label", "url"], "social_links"
        )

        try:
            SettingsService.update(data, admin_user_id=admin_id, ip_address=ip)

            # 三個圖片欄位各自獨立處理，任何一個失敗都不影響其他欄位
            # 已儲存的文字設定（它們在上面已經 commit）。
            for field_name, storage_field in (
                ("logo", "logo_path"),
                ("hero_media", "hero_media_path"),
                ("og_image", "og_image_path"),
            ):
                uploaded = getattr(form, field_name).data
                if uploaded and getattr(uploaded, "filename", ""):
                    SettingsService.update_media(
                        storage_field, uploaded, admin_user_id=admin_id, ip_address=ip
                    )

            flash("已更新網站設定。", "success")
            return redirect(url_for("admin.settings"))
        except (SettingsServiceError, MediaError) as exc:
            flash(str(exc), "error")

    if request.method == "GET":
        form.load_from(setting)

    return render_template(
        "admin/settings.html",
        form=form,
        setting=setting,
        confirm_form=ConfirmForm(),
    )


@admin_bp.route("/settings/media/<field>/delete", methods=["POST"])
def settings_media_delete(field: str):
    """移除設定中的圖片（logo / hero / OG）。"""
    if not ConfirmForm().validate_on_submit():
        abort(400)

    admin_id, ip = _actor()
    try:
        SettingsService.remove_media(field, admin_user_id=admin_id, ip_address=ip)
        flash("已移除圖片。", "success")
    except SettingsServiceError as exc:
        flash(str(exc), "error")

    return redirect(url_for("admin.settings"))


# ----------------------------------------------------------------------
# 系統
# ----------------------------------------------------------------------
@admin_bp.route("/system")
def system():
    """系統資訊（SAI §7.3 System：備份狀態、稽核、登出）。"""
    return render_template(
        "admin/system.html",
        health=HealthService.system_report(),
        last_backup=HealthService.last_backup(),
        security_events=recent_security_events(limit=15),
        redirect_count=count_redirects(),
        password_form=ChangePasswordForm(),
    )


@admin_bp.route("/password", methods=["GET", "POST"])
def change_password():
    """修改管理員密碼（SAI 附錄 A：GET/POST /admin/password）。"""
    form = ChangePasswordForm()

    if form.validate_on_submit():
        if not current_user.verify_password(form.current_password.data):
            # 不區分「舊密碼錯」的細節訊息，但這裡必須明確告知，
            # 因為使用者已通過身分驗證，不存在帳號列舉風險。
            flash("目前密碼不正確。", "error")
            return render_template("admin/change_password.html", form=form)

        try:
            current_user.set_password(form.new_password.data)
        except ValueError as exc:
            flash(str(exc), "error")
            return render_template("admin/change_password.html", form=form)

        admin_id, ip = _actor()
        AuditLog.write(
            action=AuditAction.PASSWORD_CHANGE,
            entity_type="system",
            entity_id=admin_id,
            summary=f"管理員 {current_user.username} 於後台修改密碼",
            admin_user_id=admin_id,
            ip_address=ip,
        )

        try:
            db.session.commit()
        except Exception:  # noqa: BLE001
            db.session.rollback()
            logger.exception("密碼更新失敗")
            flash("密碼更新失敗，請稍後再試。", "error")
            return render_template("admin/change_password.html", form=form)

        flash("密碼已更新。", "success")
        return redirect(url_for("admin.system"))

    return render_template("admin/change_password.html", form=form)
