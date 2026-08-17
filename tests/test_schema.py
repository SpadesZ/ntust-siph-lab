# ============================================================
# NTUST SiPh Lab - Schema & Model Constraint Tests
#
# 上下游：
#   tests/conftest.py -> 本檔 -> app/models/*、migrations/versions/*
#
# 檔案路徑：tests/test_schema.py
# 建立日期：2026-08-14 / 版本：v1.0
#
# 模組定位（SAI §20 Model 層、§8.9 索引與約束）：
#   驗證 SAI §8 定義的 7 類核心資料表、欄位約束與索引
#   確實存在於「由 migration 建立」的資料庫中。
#
#   為什麼要驗證實際的資料庫而非只看 model：
#     model 定義正確不代表 migration 有跟上。若兩者漂移，
#     本機（以 migration 建表）會出現找不到欄位的錯誤，
#     而這正是 SAI §10.2 可攜契約要防範的情況。
#
# 對應規格：
#   §8.2~§8.8 七張表的欄位
#   §8.9 UNIQUE / composite index / CheckConstraint / UTC 時間
#
# 驗證方式：
#   pytest tests/test_schema.py -v
# ============================================================

from __future__ import annotations

import pytest
from sqlalchemy import inspect, text

#: SAI §8 定義的核心資料表。
CORE_TABLES = (
    "admin_users",
    "people",
    "research_outputs",
    "research_output_people",
    "site_settings",
    "redirects",
    "audit_logs",
)


def test_all_core_tables_present(app):
    """SAI §8：7 類核心 table 全部存在。"""
    from app.extensions import db

    with app.app_context():
        existing = set(inspect(db.engine).get_table_names())

    missing = set(CORE_TABLES) - existing
    assert not missing, f"缺少核心資料表：{sorted(missing)}"


def test_models_are_all_registered_in_metadata(app):
    """models/__init__.py 必須匯總全部 model。

    若有 model 未被 import，Alembic autogenerate 會誤判為
    「應刪除該表」—— 這是 models/__init__.py 禁止刪除 import 的原因。
    """
    from app.extensions import db

    with app.app_context():
        registered = set(db.metadata.tables.keys())

    missing = set(CORE_TABLES) - registered
    assert not missing, f"以下資料表未註冊於 metadata：{sorted(missing)}"


@pytest.mark.parametrize(
    "table,index_columns",
    [
        # SAI §8.9 明列的索引。
        ("people", ["status", "publish_status", "sort_order"]),
        ("research_outputs", ["publish_status", "year", "output_type"]),
    ],
)
def test_composite_indexes_exist(app, table, index_columns):
    """SAI §8.9 指定的 composite index 必須存在。"""
    from app.extensions import db

    with app.app_context():
        indexes = inspect(db.engine).get_indexes(table)

    found = any(idx["column_names"] == index_columns for idx in indexes)
    assert found, (
        f"{table} 缺少 composite index {index_columns}；"
        f"實際索引：{[i['column_names'] for i in indexes]}"
    )


@pytest.mark.parametrize(
    "table,column",
    [("people", "slug"), ("research_outputs", "slug"), ("redirects", "old_path"),
     ("admin_users", "username")],
)
def test_unique_constraints(app, table, column):
    """SAI §8.9：slug 與 old_path 必須 UNIQUE。"""
    from app.extensions import db

    with app.app_context():
        inspector = inspect(db.engine)
        unique_cols = set()

        for idx in inspector.get_indexes(table):
            if idx.get("unique"):
                unique_cols.update(idx["column_names"])
        for uc in inspector.get_unique_constraints(table):
            unique_cols.update(uc["column_names"])

    assert column in unique_cols, f"{table}.{column} 必須有 UNIQUE 約束"


def test_output_person_link_is_unique(app, sample_person, sample_output):
    """SAI §8.9：research_output_people(output, person) UNIQUE。"""
    from sqlalchemy.exc import IntegrityError

    from app.extensions import db
    from app.models.research_output import ResearchOutputPerson

    with app.app_context():
        db.session.add(
            ResearchOutputPerson(
                research_output_id=sample_output["id"], person_id=sample_person["id"]
            )
        )
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_slug_uniqueness_is_enforced_by_database(app, sample_person):
    """DB 層的 slug UNIQUE 是最後防線（不只靠應用層檢查）。"""
    from sqlalchemy.exc import IntegrityError

    from app.extensions import db
    from app.models.person import Person

    with app.app_context():
        db.session.add(Person(slug=sample_person["slug"], name_zh="重複 slug"))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


@pytest.mark.parametrize("bad_status", ["unknown", "PUBLISHED", ""])
def test_publish_status_check_constraint(app, bad_status):
    """publish_status 只接受 draft/published/archived（SAI §7.4）。"""
    from sqlalchemy.exc import IntegrityError

    from app.extensions import db
    from app.models.person import Person

    with app.app_context():
        db.session.add(
            Person(slug=f"bad-{bad_status or 'empty'}", name_zh="測試", publish_status=bad_status)
        )
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


@pytest.mark.parametrize("bad_status", ["student", "teacher", "FACULTY"])
def test_person_status_check_constraint(app, bad_status):
    """person.status 只接受 faculty/current/alumni（SAI §8.3）。"""
    from sqlalchemy.exc import IntegrityError

    from app.extensions import db
    from app.models.person import Person

    with app.app_context():
        db.session.add(Person(slug=f"bad-{bad_status}", name_zh="測試", status=bad_status))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_output_type_check_constraint(app):
    """output_type 只接受 SAI §8.4 定義的七種值。"""
    from sqlalchemy.exc import IntegrityError

    from app.extensions import db
    from app.models.research_output import ResearchOutput

    with app.app_context():
        db.session.add(ResearchOutput(slug="bad-type", year=2026, output_type="blog_post"))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_year_range_check_constraint(app):
    """year 必須在合理範圍內（避免 typo 產生 20266）。"""
    from sqlalchemy.exc import IntegrityError

    from app.extensions import db
    from app.models.research_output import ResearchOutput

    with app.app_context():
        db.session.add(ResearchOutput(slug="bad-year", year=20266, output_type="other"))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_audit_action_check_constraint(app):
    """audit_logs.action 只接受定義過的值。"""
    from sqlalchemy.exc import IntegrityError

    from app.extensions import db
    from app.models.audit_log import AuditLog

    with app.app_context():
        db.session.add(AuditLog(action="hacked"))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_redirect_status_code_constraint(app):
    """redirects.status_code 只允許 301/302。"""
    from sqlalchemy.exc import IntegrityError

    from app.extensions import db
    from app.models.redirect import Redirect

    with app.app_context():
        db.session.add(Redirect(old_path="/a", new_path="/b", status_code=418))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_timestamps_are_stored_as_utc(app, sample_person):
    """SAI §8.9：所有 datetime 以 UTC 儲存。"""
    from datetime import timezone

    from app.extensions import db
    from app.models.person import Person

    with app.app_context():
        person = db.session.get(Person, sample_person["id"])

        assert person.created_at is not None
        assert person.updated_at is not None
        # SQLAlchemy 以 timezone-aware 形式回傳。
        assert person.created_at.tzinfo is not None
        assert person.created_at.utcoffset() == timezone.utc.utcoffset(None)


def test_updated_at_changes_on_modification(app, sample_person):
    """updated_at 會在修改時自動更新（sitemap lastmod 依賴此行為）。"""
    import time

    from app.extensions import db
    from app.models.person import Person

    with app.app_context():
        person = db.session.get(Person, sample_person["id"])
        original = person.updated_at

        time.sleep(0.01)
        person.bio_zh = "修改後的簡介"
        db.session.commit()
        db.session.refresh(person)

        assert person.updated_at > original


def test_cascade_delete_removes_link_rows(app, sample_person, sample_output):
    """刪除成果時連帶刪除關聯 row，不留孤兒資料。"""
    from sqlalchemy import func, select

    from app.extensions import db
    from app.models.research_output import ResearchOutput, ResearchOutputPerson

    with app.app_context():
        output = db.session.get(ResearchOutput, sample_output["id"])
        db.session.delete(output)
        db.session.commit()

        remaining = db.session.scalar(select(func.count(ResearchOutputPerson.id)))
        assert remaining == 0


def test_audit_log_survives_admin_deletion(app, admin_user):
    """稽核紀錄必須比帳號長壽（ON DELETE SET NULL）。"""
    from sqlalchemy import select

    from app.extensions import db
    from app.models.admin_user import AdminUser
    from app.models.audit_log import AuditLog
    from app.models.mixins import AuditAction

    with app.app_context():
        AuditLog.write(
            action=AuditAction.LOGIN,
            entity_type="system",
            summary="測試登入",
            admin_user_id=admin_user["id"],
        )
        db.session.commit()

        user = db.session.get(AdminUser, admin_user["id"])
        db.session.delete(user)
        db.session.commit()

        entries = db.session.scalars(select(AuditLog)).all()
        assert len(entries) == 1, "刪除帳號不得連帶刪除稽核紀錄"
        assert entries[0].admin_user_id is None


def test_sqlite_foreign_keys_enforced(app):
    """SQLite 的外鍵約束必須開啟（對齊 PostgreSQL 行為）。

    這是 SAI §24 列為高風險的「SQLite -> PostgreSQL 行為差異」
    的具體防護：未開啟時孤兒資料不會被擋，
    同樣的程式在 PostgreSQL 會失敗。
    """
    from app.extensions import db

    with app.app_context():
        if not db.engine.url.drivername.startswith("sqlite"):
            pytest.skip("僅適用於 SQLite")

        with db.engine.connect() as conn:
            value = conn.execute(text("PRAGMA foreign_keys")).scalar()
        assert value == 1, "SQLite 的 foreign_keys PRAGMA 必須為開啟"


# ----------------------------------------------------------------------
# 作者結構化資料（交付前審查 REV-106）
# ----------------------------------------------------------------------
# 沒有 Lab 人物關聯時，成果會退回使用 authors_display_text。
# 那是一整串作者列，若直接輸出成單一 Person.name，等於宣告一個
# 姓名叫 "C.-L. Yang, A. B. Chen, D. Lin" 的人 —— 與頁面意思不符，
# 違反 [S7]「structured data 必須正確代表頁面主內容」。


def test_author_display_text_is_split_into_separate_persons(app):
    """純文字作者列必須拆成多個 Person，而不是一個超長姓名。"""
    from app.services.research_service import ResearchService
    from app.services.schema_service import SchemaService

    # test_request_context：SchemaService 會用 url_for 產生 canonical，
    # 需要 request context 才能 build URL。
    with app.test_request_context():
        output = ResearchService.create({
            "title_zh": "作者拆分測試",
            "slug": "author-split-test",
            "output_type": "journal",
            "year": 2026,
            "summary_zh": "測試用摘要。",
            "authors_display_text": "C.-L. Yang, A. B. Chen, D. Lin",
        })
        data = SchemaService.research_output(output)

    authors = data["author"]
    assert isinstance(authors, list), "多位作者必須輸出為陣列"
    assert [a["name"] for a in authors] == ["C.-L. Yang", "A. B. Chen", "D. Lin"]
    assert all(a["@type"] == "Person" for a in authors)


def test_single_author_display_text_stays_single_person(app):
    """只有一位作者時行為與拆分前一致（不得變成單元素陣列）。"""
    from app.services.research_service import ResearchService
    from app.services.schema_service import SchemaService

    # test_request_context：SchemaService 會用 url_for 產生 canonical，
    # 需要 request context 才能 build URL。
    with app.test_request_context():
        output = ResearchService.create({
            "title_zh": "單一作者測試",
            "slug": "author-single-test",
            "output_type": "journal",
            "year": 2026,
            "summary_zh": "測試用摘要。",
            "authors_display_text": "C.-L. Yang",
        })
        data = SchemaService.research_output(output)

    assert data["author"] == {"@type": "Person", "name": "C.-L. Yang"}


def test_orphan_foreign_key_is_rejected(app, sample_output):
    """指向不存在人物的關聯必須被 DB 拒絕。"""
    from sqlalchemy.exc import IntegrityError

    from app.extensions import db
    from app.models.research_output import ResearchOutputPerson

    with app.app_context():
        db.session.add(
            ResearchOutputPerson(
                research_output_id=sample_output["id"], person_id=987654
            )
        )
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_site_setting_is_singleton(app):
    """SiteSetting 永遠只有一列（id=1）。"""
    from sqlalchemy import func, select

    from app.extensions import db
    from app.models.site_setting import SiteSetting

    with app.app_context():
        first = SiteSetting.get()
        second = SiteSetting.get()

        assert first.id == second.id == 1
        assert db.session.scalar(select(func.count(SiteSetting.id))) == 1


def test_no_native_enum_types_used(app):
    """SAI §10.2：不得使用 native ENUM 型別。

    PostgreSQL 的 ENUM 新增值需要額外 migration，
    SQLite 則沒有此型別。統一使用 VARCHAR + CheckConstraint。
    """
    from app.extensions import db

    with app.app_context():
        for table in CORE_TABLES:
            for column in inspect(db.engine).get_columns(table):
                type_name = type(column["type"]).__name__.lower()
                assert "enum" not in type_name, (
                    f"{table}.{column['name']} 使用了 ENUM 型別，違反 SAI §10.2"
                )
