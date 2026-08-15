# ============================================================
# NTUST SiPh Lab - Pytest Fixtures
#
# 上下游：
#   pytest -> conftest.py（本檔）-> create_app('test')
#       -> 暫存 SQLite + 暫存 uploads
#       -> 各 tests/test_*.py 使用 fixture
#
# 檔案路徑：
#   tests/conftest.py
#
# 建立日期：2026-08-14
# 最後重大修改：2026-08-14
# 版本：v1.0
#
# 模組定位與責任邊界：
#   提供所有測試共用的 fixture。SAI §20 定義的測試層級
#   （Unit / Model / Route / Security / SEO / Migration /
#   Backup / UI）全部依賴這裡建立的環境。
#
#   責任邊界（不得做的事）：
#     - 不得使用開發用的 instance/siph_lab.db（會污染實際資料）。
#     - 不得寫入專案的 uploads/ 目錄。
#     - 不得依賴網際網路（測試必須可離線執行）。
#
# 輸入 -> 處理 -> 輸出 Pipeline：
#   pytest 啟動
#     -> 建立暫存目錄（DB + uploads）
#     -> create_app('test') 並套用暫存路徑
#     -> flask db upgrade（而非 create_all，見下方說明）
#     -> yield app / client / 各種資料 fixture
#     -> 測試結束後刪除暫存目錄
#
# 主要 Fixture：
#   app              - 已建表的 Flask app（function scope）
#   client           - 測試用 HTTP client
#   runner           - CLI runner
#   admin_user       - 已建立的管理員帳號
#   logged_in_client - 已登入的 client
#   sample_person    - 已發布的在學成員
#   sample_output    - 已發布的研究成果
#   csrf_app / csrf_client - 開啟 CSRF 的 app（安全測試用）
#
# 依賴套件：pytest, flask, flask_migrate
#
# 環境變數：
#   本檔會設定 APP_ENV=test 與暫存路徑，不讀取使用者環境。
#
# 資料庫使用方式：
#   每個測試函式使用「獨立的暫存 SQLite 檔案」。
#
#   為什麼用檔案而非 :memory:：
#     SQLAlchemy 對 :memory: 的每條新連線都會得到全新的空資料庫。
#     Flask-Migrate 的 upgrade 與後續查詢可能使用不同連線，
#     導致「migration 跑完了但查不到表」。使用暫存檔案可讓
#     所有連線看到同一個資料庫。
#
#   為什麼用 flask db upgrade 而非 db.create_all()：
#     SAI §10.2 要求「Alembic migration 必須在 SQLite test 與
#     PostgreSQL migration test 都能從 empty database 升到 head」。
#     若測試改用 create_all()，migration 檔案的正確性就永遠
#     不會被測到，等到部署 PostgreSQL 才會發現問題。
#     以 upgrade 建表等於每次跑測試都在驗證 migration。
#
# Error Handling / Fallback：
#   暫存目錄以 tempfile 建立並在 teardown 刪除。
#   即使測試失敗也會清理（fixture 的 yield 之後一定執行）。
#
# 特殊機制（CSRF 與 rate limit）：
#   TestConfig 預設關閉 CSRF 與 rate limit，讓一般功能測試
#   不必每次抓 token。但 SAI §20 要求這兩項本身必須被測到，
#   因此另外提供 csrf_app fixture 局部重新開啟 ——
#   見 config.py 的 TestConfig docstring。
#
# 已知限制與禁止事項：
#   1. 禁止讓測試依賴執行順序（每個測試都有獨立資料庫）。
#   2. 禁止在測試中連線真實的 GCS 或外部網站。
#
# 維護契約：
#   新增需要初始資料的測試時，優先新增 fixture 而非在測試內
#   重複建立 —— 重複的建立邏輯會在 model 變更時散落各處失效。
#
# 驗證方式：
#   pytest -q
# ============================================================

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

# 讓 tests/ 可以 import app 與 scripts。
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

#: 測試用的管理員帳密。長度符合 AdminUser.set_password 的 12 字元下限。
TEST_ADMIN_USERNAME = "testadmin"
TEST_ADMIN_PASSWORD = "test-password-12345"


def _build_app(*, enable_csrf: bool = False, enable_limiter: bool = False):
    """建立測試用 app 與其暫存目錄。

    Returns:
        (app, temp_dir) —— 呼叫端負責刪除 temp_dir。
    """
    from app import create_app
    from app.extensions import db

    temp_dir = Path(tempfile.mkdtemp(prefix="siph-test-"))
    db_path = temp_dir / "test.db"
    upload_dir = temp_dir / "uploads"
    backup_dir = temp_dir / "backups"
    upload_dir.mkdir(parents=True, exist_ok=True)
    backup_dir.mkdir(parents=True, exist_ok=True)

    # 所有覆寫都必須在 create_app 內部的 extension 初始化「之前」套用，
    # 否則 Flask-Limiter 與 storage 會使用預設值。
    # 見 create_app 的 config_overrides 說明。
    app = create_app(
        "test",
        config_overrides={
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path.as_posix()}",
            "UPLOAD_DIR": str(upload_dir),
            "BACKUP_DIR": str(backup_dir),
            "WTF_CSRF_ENABLED": enable_csrf,
            "RATELIMIT_ENABLED": enable_limiter,
            "PUBLIC_BASE_URL": "http://localhost",
        },
    )

    with app.app_context():
        # 以 Alembic migration 建表（見檔頭說明）。
        from flask_migrate import upgrade

        upgrade(directory=str(PROJECT_ROOT / "migrations"))
        db.session.remove()

    return app, temp_dir


@pytest.fixture()
def app():
    """標準測試 app（CSRF 與 rate limit 關閉）。"""
    application, temp_dir = _build_app()
    yield application

    from app.extensions import db

    with application.app_context():
        db.session.remove()
        db.engine.dispose()
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture()
def csrf_app():
    """開啟 CSRF 的 app（SAI §20 Security 層測試用）。"""
    application, temp_dir = _build_app(enable_csrf=True)
    yield application

    from app.extensions import db

    with application.app_context():
        db.session.remove()
        db.engine.dispose()
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture()
def limiter_app():
    """開啟 rate limit 的 app（AC-02 測試用）。"""
    application, temp_dir = _build_app(enable_limiter=True)
    yield application

    from app.extensions import db, limiter

    with application.app_context():
        db.session.remove()
        db.engine.dispose()
    # 清除限流計數，避免影響其他測試。
    try:
        limiter.reset()
    except Exception:  # noqa: BLE001 - 不同儲存後端的 reset 支援度不同
        pass
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture()
def client(app):
    """測試用 HTTP client。"""
    return app.test_client()


@pytest.fixture()
def runner(app):
    """CLI runner（測試 flask admin / seed 指令）。"""
    return app.test_cli_runner()


@pytest.fixture()
def admin_user(app):
    """建立並回傳管理員帳號。"""
    from app.extensions import db
    from app.models.admin_user import AdminUser

    with app.app_context():
        user = AdminUser(username=TEST_ADMIN_USERNAME)
        user.set_password(TEST_ADMIN_PASSWORD)
        db.session.add(user)
        db.session.commit()
        # 取出純資料，避免 detached instance 問題。
        return {"id": user.id, "username": user.username}


@pytest.fixture()
def logged_in_client(app, admin_user):
    """已登入的 client。"""
    test_client = app.test_client()
    response = test_client.post(
        "/admin/login",
        data={"username": TEST_ADMIN_USERNAME, "password": TEST_ADMIN_PASSWORD},
        follow_redirects=False,
    )
    assert response.status_code == 302, "測試用登入失敗，後續測試無意義"
    return test_client


@pytest.fixture()
def sample_person(app):
    """建立一位已發布的在學成員。

    回傳 dict 而非 model 物件：測試會在不同的 app context 中
    使用它，直接傳 ORM 物件會遇到 DetachedInstanceError。
    """
    from app.models.mixins import PersonStatus
    from app.services.person_service import PersonService

    with app.app_context():
        person = PersonService.create(
            {
                "name_zh": "測試成員",
                "name_en": "Test Member",
                "status": PersonStatus.CURRENT,
                "research_focus_zh": "矽光子元件設計與量測",
                "skills": "矽光子, 光通訊",
                "title_zh": "碩二生",
            }
        )
        PersonService.publish(person)
        return {"id": person.id, "slug": person.slug, "name_zh": person.name_zh}


@pytest.fixture()
def draft_person(app):
    """建立一位「未發布」的成員（測試 draft 不外洩）。"""
    from app.models.mixins import PersonStatus
    from app.services.person_service import PersonService

    with app.app_context():
        person = PersonService.create(
            {
                "name_zh": "草稿成員",
                "status": PersonStatus.CURRENT,
                "research_focus_zh": "尚未公開的研究方向",
            }
        )
        return {"id": person.id, "slug": person.slug, "name_zh": person.name_zh}


@pytest.fixture()
def sample_output(app, sample_person):
    """建立一筆已發布的研究成果，並關聯 sample_person。"""
    from app.models.mixins import OutputType
    from app.services.research_service import ResearchService

    with app.app_context():
        output = ResearchService.create(
            {
                "output_type": OutputType.JOURNAL,
                "year": 2026,
                "title_zh": "微環諧振器於光通道效能監視之應用",
                "title_en": "Microring Resonator for Optical Performance Monitoring",
                "summary_zh": (
                    "本研究提出以矽光子微環諧振器實現光通道效能監視的方法，"
                    "在不中斷傳輸的前提下量測通道品質，並以模擬與實測驗證其可行性。"
                ),
                "method_zh": "以 FDTD 模擬微環結構，並於矽光子平台上製作元件進行量測。",
                "results_zh": "在 1550 nm 波段量得品質因子約 1.2 萬，監視誤差低於 0.5 dB。",
                "significance_zh": "提供低成本的線上監視方案；目前僅驗證單通道情境。",
                "venue": "Journal of Lightwave Technology",
                "doi": "10.1109/JLT.2026.1234567",
                "keywords": "矽光子, 微環諧振器, 光通道效能監視",
                "people": [sample_person["id"]],
            }
        )
        ResearchService.publish(output)
        return {"id": output.id, "slug": output.slug, "title": output.display_title}


@pytest.fixture()
def png_bytes():
    """產生一張合法的 1x1 PNG（測試上傳流程）。

    以 Pillow 即時產生而非讀取檔案：避免測試相依於外部素材，
    也確保產出的一定是 Pillow 能解碼的合法影像。
    """
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (12, 12), color=(0, 166, 166)).save(buffer, format="PNG")
    buffer.seek(0)
    return buffer.getvalue()
