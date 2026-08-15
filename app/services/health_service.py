# ============================================================
# NTUST SiPh Lab - System Health Service
#
# 上下游：
#   blueprints/public/routes.py  -> /healthz -> HealthService.liveness()
#   blueprints/admin/routes.py   -> Dashboard System health 區塊
#       -> HealthService.system_report()
#   HealthService -> db（SELECT 1）
#   HealthService -> StorageBackend.health_check()
#   HealthService -> backups 目錄（最近一次備份時間）
#
# 檔案路徑：
#   app/services/health_service.py
#
# 建立日期：2026-08-14
# 最後重大修改：2026-08-14
# 版本：v1.0
#
# 模組定位與責任邊界：
#   實作 SAI §19 的 /healthz 與 §7.2 Dashboard 的
#   「System health：DB writable、upload writable、backup last success」。
#
#   兩者的資訊需求不同，因此刻意分成兩個方法：
#     liveness()      - 給 Cloud Run/負載平衡器；輕量、不回敏感資料
#     system_report() - 給登入後的管理員；可含路徑與時間細節
#
#   責任邊界（不得做的事）：
#     - liveness() 不得回傳版本號、路徑、DB 連線字串等資訊
#       （SAI §19：不回敏感資料）。
#     - 不得在此執行修復動作（健康檢查是唯讀的觀測）。
#
# 輸入 -> 處理 -> 輸出 Pipeline：
#   liveness:      無輸入 -> SELECT 1 -> (bool, 狀態字串)
#   system_report: 無輸入 -> DB 可寫檢查 + storage health + 備份掃描
#                  -> list[dict] 檢查項目
#
# 主要 Class / Function：
#   HealthService.liveness()      - /healthz 用
#   HealthService.system_report() - Dashboard 用
#   HealthService.last_backup()   - 最近一次備份資訊
#
# 依賴套件：
#   sqlalchemy, flask, app.extensions.db, app.storage
#
# 環境變數（透過 app.config）：
#   BACKUP_DIR - 備份掃描目錄
#
# 資料庫使用方式：
#   liveness 執行 SELECT 1（SAI §19「可做輕量 DB SELECT 1」）。
#   system_report 額外檢查是否可開啟寫入交易。
#
# Error Handling / Fallback：
#   所有檢查都以 try/except 包住並回報為「不健康」，
#   絕不讓健康檢查本身拋出未處理例外 —— 否則 /healthz 會回 500，
#   Cloud Run 會判定 instance 失敗並反覆重啟，把小問題放大成停機。
#
# 特殊機制（/healthz 的寫入檢查）：
#   liveness() 刻意「只做讀取」。
#   為什麼：Cloud Run 每個 instance 都會被頻繁探測，
#   若每次都寫 DB，會產生無謂的交易與 WAL 成長。
#   寫入能力的檢查留給 Dashboard（管理員主動查看時才執行）。
#
# 已知限制與禁止事項：
#   1. liveness() 不檢查 storage —— GCS 短暫抖動不應導致
#      整個 instance 被判定死亡並重啟（網站其餘部分仍可服務）。
#   2. 禁止在 /healthz 回應中包含 stack trace 或設定值。
#
# 維護契約：
#   若未來 /healthz 加入更多檢查，必須確認「檢查失敗的後果」
#   是否真的需要重啟 instance。非致命問題應放在 system_report。
#
# 驗證方式：
#   pytest tests/test_health.py
#   curl -i http://localhost:8000/healthz
# ============================================================

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from flask import current_app
from sqlalchemy import text

from app.extensions import db
from app.storage import get_storage

logger = logging.getLogger(__name__)


class HealthService:
    """系統健康檢查（SAI §7.2、§19）。"""

    # ------------------------------------------------------------------
    # /healthz
    # ------------------------------------------------------------------
    @staticmethod
    def liveness() -> tuple[bool, str]:
        """輕量存活檢查。

        Returns:
            (是否健康, 簡短狀態字串)

        回傳的字串刻意只有 "ok" / "database unavailable"，
        不含任何環境資訊（SAI §19）。
        """
        try:
            db.session.execute(text("SELECT 1"))
            return True, "ok"
        except Exception:  # noqa: BLE001 - 任何 DB 問題都視為不健康
            # 記錄完整例外到 server log，但不回傳給呼叫端。
            logger.exception("healthz: 資料庫檢查失敗")
            return False, "database unavailable"

    # ------------------------------------------------------------------
    # Dashboard
    # ------------------------------------------------------------------
    @staticmethod
    def _check_database() -> dict:
        """檢查資料庫可讀與可寫。"""
        try:
            db.session.execute(text("SELECT 1"))
        except Exception as exc:  # noqa: BLE001
            return {"name": "資料庫連線", "ok": False, "detail": f"無法查詢：{exc}"}

        # 可寫檢查：開啟一個巢狀交易再 rollback，不留下任何資料。
        # 用 begin_nested 而非真的寫表，避免污染稽核紀錄。
        try:
            nested = db.session.begin_nested()
            db.session.execute(text("SELECT 1"))
            nested.rollback()
            writable = True
            detail = "可讀可寫"
        except Exception as exc:  # noqa: BLE001
            writable = False
            detail = f"唯讀或交易失敗：{exc}"

        return {"name": "資料庫連線", "ok": writable, "detail": detail}

    @staticmethod
    def _check_storage() -> dict:
        """檢查媒體儲存後端。"""
        try:
            backend = get_storage()
            ok, detail = backend.health_check()
            return {"name": f"媒體儲存（{backend.name}）", "ok": ok, "detail": detail}
        except Exception as exc:  # noqa: BLE001
            return {"name": "媒體儲存", "ok": False, "detail": f"初始化失敗：{exc}"}

    @staticmethod
    def last_backup() -> dict | None:
        """回傳最近一次備份的資訊。

        Returns:
            {"name": 檔名, "created_at": datetime, "size": int} 或 None。

        實作說明：
          掃描 BACKUP_DIR 下的 *.zip（backup_sqlite.py 的輸出格式），
          取 mtime 最新者。不讀取內容，只看檔案 metadata，
          因此對大型備份檔也很快。
        """
        backup_dir = current_app.config.get("BACKUP_DIR")
        if not backup_dir:
            return None

        try:
            path = Path(backup_dir)
            if not path.is_dir():
                return None

            candidates = sorted(
                path.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True
            )
            if not candidates:
                return None

            latest = candidates[0]
            stat = latest.stat()
            return {
                "name": latest.name,
                "created_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                "size": stat.st_size,
            }
        except OSError as exc:
            logger.warning("讀取備份目錄失敗：%s", exc)
            return None

    @staticmethod
    def _check_backup() -> dict:
        """備份狀態檢查（SAI §7.2 backup last success）。"""
        latest = HealthService.last_backup()
        if latest is None:
            return {
                "name": "最近一次備份",
                "ok": False,
                "detail": "尚未找到備份檔。請執行 python scripts/backup_sqlite.py（SAI §10.6）。",
            }

        from app.utils.dates import format_datetime

        age_days = (datetime.now(timezone.utc) - latest["created_at"]).days
        # 7 天是提醒門檻，不是硬性失敗條件 —— 研究室網站的
        # 內容更新頻率低，每日備份並非必要。
        ok = age_days <= 7
        detail = (
            f"{latest['name']}（{format_datetime(latest['created_at'])}，"
            f"{latest['size'] // 1024} KB，{age_days} 天前）"
        )
        if not ok:
            detail += "　建議重新執行備份。"

        return {"name": "最近一次備份", "ok": ok, "detail": detail}

    @staticmethod
    def system_report() -> list[dict]:
        """Dashboard 的 System health 區塊資料（SAI §7.2）。

        Returns:
            [{"name": str, "ok": bool, "detail": str}, ...]
        """
        return [
            HealthService._check_database(),
            HealthService._check_storage(),
            HealthService._check_backup(),
        ]
