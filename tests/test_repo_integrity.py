# ============================================================
# NTUST SiPh Lab - Repository Integrity Tests
#
# 檔案路徑：tests/test_repo_integrity.py
# 建立日期：2026-08-15 / 版本：v1.0
#
# 模組定位（SAI §9.4 目錄樹、§23.1 維護契約）：
#   本專案的檔頭註解規範要求每支程式都寫出「驗證方式」與
#   相關檔案路徑。這些引用一旦指向不存在的檔案，整份文件的
#   可信度就被折損 —— 維護者會花時間找不存在的東西，
#   然後學會忽略所有註解。
#
#   交付審查時曾出現 37 個被引用路徑中有 22 個不存在（59%），
#   其中包含 11 個「驗證方式」指定的測試檔。本檔把這件事
#   自動化，讓它不可能再度發生。
#
# 涵蓋：
#   1. SAI §9.4 目錄樹要求的檔案全部存在
#   2. 程式碼與文件中引用的專案路徑全部存在
#   3. pytest.ini 宣告的 marker 全部有被使用
#
# 為什麼把「文件完整性」當測試：
#   §9.4 的目錄樹是規格的一部分，不是建議。把它變成可執行的
#   斷言，等於讓「漏交檔案」在 CI 就被擋下，而不是等到
#   交付審查才被發現。
#
# 驗證方式：
#   pytest tests/test_repo_integrity.py -v
# ============================================================

from __future__ import annotations

import configparser
import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: 掃描時忽略的目錄。
_IGNORED_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache",
                 ".mypy_cache", ".ruff_cache", "node_modules", "instance",
                 "uploads", "backups", "htmlcov"}

#: 會被掃描引用路徑的副檔名。
_SCANNED_SUFFIXES = {".py", ".md", ".html", ".txt", ".yml", ".yaml",
                     ".ini", ".css", ".example"}

#: 專案內部路徑的引用樣式。
_REFERENCE_PATTERN = re.compile(
    r"(?<![\w./-])("
    r"docs/[\w.\-]+\.md"
    r"|deploy/[\w.\-]+\.(?:md|yaml|yml)"
    r"|scripts/[\w.\-]+\.py"
    r"|tests/test_[\w]+\.py"
    r"|skills/[\w\-]+/SKILL\.md"
    r"|migrations/[\w.\-/]+\.py"
    r")"
)


def _iter_project_files():
    for path in PROJECT_ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in _IGNORED_DIRS for part in path.parts):
            continue
        if path.suffix not in _SCANNED_SUFFIXES and path.name != "Dockerfile":
            continue
        yield path


# ----------------------------------------------------------------------
# 1. SAI §9.4 目錄樹
# ----------------------------------------------------------------------
#: SAI §9.4 明列且本專案必須交付的檔案。
_REQUIRED_FILES = [
    "README.md",
    ".env.example",
    ".gitignore",
    "Dockerfile",
    "docker-compose.yml",
    "requirements.txt",
    "requirements-dev.txt",
    "gunicorn.conf.py",
    "wsgi.py",
    # scripts（§9.4 + 附錄 E）
    "scripts/seed_from_google_sites.py",
    "scripts/backup_sqlite.py",
    "scripts/export_sqlite.py",
    "scripts/import_postgres.py",
    "scripts/sync_media_to_gcs.py",
    "scripts/verify_migration.py",
    "scripts/smoke_cloud.py",
    # deploy
    "deploy/cloudrun.md",
    "deploy/service.yaml",
    "deploy/migration-runbook.md",
    # legacy 證據鏈（§22）
    "legacy/google_sites/source_snapshot.md",
    "legacy/google_sites/migration_inventory.csv",
    "legacy/google_sites/content_mapping.csv",
    "legacy/google_sites/media_manifest.csv",
    "legacy/google_sites/difference_report.md",
    "legacy/google_sites/content_signoff.md",
    # skills（§14）
    "skills/siph-lab-web-design/SKILL.md",
    "skills/siph-lab-seo-geo/SKILL.md",
    # docs（§9.4）
    "docs/SAI.md",
    "docs/database.md",
    "docs/content-guide.md",
    "docs/local-development.md",
    "docs/cloudrun-deployment.md",
    "docs/acceptance-checklist.md",
]


@pytest.mark.parametrize("relative", _REQUIRED_FILES)
def test_sai_required_file_exists(relative):
    """SAI §9.4 目錄樹要求的檔案必須存在。"""
    path = PROJECT_ROOT / relative
    assert path.is_file(), f"SAI §9.4 要求的檔案不存在：{relative}"


def test_required_files_are_not_empty():
    """存在但空白的檔案等同沒交（交付審查時 docs/ 與 skills/ 正是空目錄）。"""
    empty = [
        rel for rel in _REQUIRED_FILES
        if (PROJECT_ROOT / rel).is_file() and (PROJECT_ROOT / rel).stat().st_size < 50
    ]
    assert not empty, f"以下必要檔案內容過少，形同未交付：{empty}"


# ----------------------------------------------------------------------
# 2. 引用路徑必須存在
# ----------------------------------------------------------------------
def test_no_dangling_path_references():
    """程式碼與文件中引用的專案路徑必須存在。

    檔頭的「驗證方式」若指向不存在的測試檔，維護者會找不到東西，
    進而對整份註解失去信任。
    """
    dangling: dict[str, set[str]] = {}

    for path in _iter_project_files():
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for match in _REFERENCE_PATTERN.findall(content):
            if not (PROJECT_ROOT / match).exists():
                dangling.setdefault(match, set()).add(
                    str(path.relative_to(PROJECT_ROOT))
                )

    report = "\n  ".join(
        f"{target}  <- {', '.join(sorted(sources))}"
        for target, sources in sorted(dangling.items())
    )
    assert not dangling, f"以下引用的路徑不存在：\n  {report}"


# ----------------------------------------------------------------------
# 3. pytest marker
# ----------------------------------------------------------------------
def test_declared_markers_are_used():
    """pytest.ini 宣告的 marker 必須真的被使用。

    交付審查時 pytest.ini 宣告並在註解中教學 `pytest -m acceptance`，
    但該 marker 使用次數為 0 —— 那個指令永遠回傳空集合。
    宣告卻不使用的 marker 屬於「看起來有做」的文件債。
    """
    parser = configparser.ConfigParser()
    parser.read(PROJECT_ROOT / "pytest.ini", encoding="utf-8")
    declared = {
        line.split(":", 1)[0].strip()
        for line in parser.get("pytest", "markers", fallback="").splitlines()
        if line.strip()
    }
    assert declared, "pytest.ini 應宣告 marker"

    test_source = "\n".join(
        p.read_text(encoding="utf-8") for p in (PROJECT_ROOT / "tests").glob("test_*.py")
    )

    unused = {m for m in declared if f"pytest.mark.{m}" not in test_source}
    assert not unused, (
        f"以下 marker 已宣告但未被任何測試使用：{sorted(unused)}。"
        "請套用到對應測試，或從 pytest.ini 移除。"
    )


def test_acceptance_marker_covers_enough_criteria():
    """acceptance marker 應涵蓋多數 AC 條目，而非只標一兩個充數。"""
    test_source = "\n".join(
        p.read_text(encoding="utf-8") for p in (PROJECT_ROOT / "tests").glob("test_*.py")
    )
    count = test_source.count("pytest.mark.acceptance")
    assert count >= 26, f"acceptance 測試僅 {count} 個，應至少涵蓋 AC-01~AC-26"


# ----------------------------------------------------------------------
# 4. 檔頭註解規範
# ----------------------------------------------------------------------
def test_application_modules_have_header_comments():
    """app/ 下的實作模組必須有專案規範的檔頭區塊。

    __init__.py 等套件標記檔內容稀薄，允許較寬鬆的標準。
    """
    required = ["檔案路徑", "模組定位", "驗證方式"]
    offenders = []

    for path in (PROJECT_ROOT / "app").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        if path.name == "__init__.py" and path.stat().st_size < 4000:
            continue
        head = path.read_text(encoding="utf-8")[:6000]
        missing = [key for key in required if key not in head]
        if missing:
            offenders.append(f"{path.relative_to(PROJECT_ROOT)}（缺 {missing}）")

    assert not offenders, "以下模組缺少檔頭區塊：\n  " + "\n  ".join(offenders)
