# ============================================================
# NTUST SiPh Lab - Datetime Display Helpers
#
# 上下游：
#   models（DB 存 UTC）-> 本模組 -> Jinja filter -> 前台/後台顯示
#   services/seo_service.py -> iso_date() -> JSON-LD datePublished
#   blueprints/public/sitemap -> iso_datetime() -> <lastmod>
#
# 檔案路徑：
#   app/utils/dates.py
#
# 建立日期：2026-08-14
# 最後重大修改：2026-08-14
# 版本：v1.0
#
# 模組定位與責任邊界：
#   實作 SAI §8.9「所有 datetime 以 UTC 儲存；前台需要時以
#   Asia/Taipei 顯示」。轉換只發生在顯示層，資料庫與 API
#   一律保持 UTC。
#
#   責任邊界（不得做的事）：
#     - 不得把轉換後的本地時間寫回資料庫。
#     - 不得在此決定顯示格式的文案（由 template 選擇 filter）。
#
# 輸入 -> 處理 -> 輸出 Pipeline：
#   UTC aware datetime -> ZoneInfo("Asia/Taipei") -> 格式化字串
#
# 主要 Function：
#   to_local(dt)        - UTC -> Asia/Taipei
#   format_date(dt)     - "2026-08-14"
#   format_datetime(dt) - "2026-08-14 22:41"
#   iso_date(dt)        - ISO-8601 日期（JSON-LD / sitemap 用）
#   iso_datetime(dt)    - ISO-8601 完整時間（含時區）
#
# 依賴套件：
#   標準庫 datetime / zoneinfo（Python 3.9+）
#   Windows 需要 tzdata 套件才有 IANA 時區資料庫 —— 已列入
#   requirements.txt，見下方 Fallback 說明。
#
# 環境變數：
#   本模組使用固定的 Asia/Taipei（config.DISPLAY_TIMEZONE 為
#   同一值的宣告式紀錄）。研究室位於台灣，不需要多時區支援。
#
# 資料庫使用方式：無。
#
# Error Handling / Fallback：
#   - 若 zoneinfo 找不到 Asia/Taipei（缺 tzdata 的精簡環境），
#     退回固定 UTC+8 偏移。台灣自 1980 年起不實施日光節約時間，
#     固定偏移在本案情境下與 IANA 結果一致，因此這個 fallback
#     不會產生錯誤時間。
#   - 所有函式對 None 回傳空字串或 None，不拋錯，
#     避免 template 因為缺少 last_login_at 而 500。
#
# 特殊機制（naive datetime 相容）：
#   SQLite 在某些舊資料或直接以 SQL 寫入的情況下可能回傳
#   naive datetime。本模組一律先假定 naive 值為 UTC 再轉換，
#   而不是拋錯 —— 這讓 restore drill 匯入的舊資料仍能顯示。
#
# 已知限制與禁止事項：
#   1. 禁止在 model 的 default 使用本模組（那裡必須用
#      mixins.utcnow，保持 DB 一律 UTC）。
#   2. 不支援使用者自選時區；若未來需要，應在 SiteSetting
#      新增欄位而非在此硬編碼多個時區。
#
# 維護契約：
#   修改顯示格式時必須確認 sitemap 的 <lastmod> 仍為合法
#   W3C Datetime 格式，否則 Search Console 會回報 sitemap 錯誤。
#
# 驗證方式：
#   pytest tests/test_seo.py::test_sitemap_lastmod_is_valid_w3c_datetime
# ============================================================

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

#: 顯示時區。研究室位於台灣（SAI §8.9）。
_TAIPEI_NAME = "Asia/Taipei"

try:  # pragma: no cover - 依環境是否有 tzdata 而定
    from zoneinfo import ZoneInfo

    TAIPEI = ZoneInfo(_TAIPEI_NAME)
except Exception:  # noqa: BLE001 - 缺 tzdata 時退回固定偏移
    # 台灣自 1980 年起無日光節約時間，固定 UTC+8 與 IANA 一致。
    TAIPEI = timezone(timedelta(hours=8), name="UTC+8")


def _ensure_aware(value: datetime) -> datetime:
    """把 naive datetime 視為 UTC 並補上 tzinfo。

    為什麼假定 naive 是 UTC 而不是本地時間：
      本專案所有寫入路徑都使用 mixins.utcnow()（aware UTC）。
      唯一會出現 naive 值的情況是「外部工具直接寫入 SQLite」
      （例如 restore drill 或手動 SQL）。那些值依專案慣例也是 UTC，
      因此假定 UTC 是正確的還原。
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def to_local(value: datetime | None) -> datetime | None:
    """UTC datetime 轉 Asia/Taipei。None 進 None 出。"""
    if value is None:
        return None
    return _ensure_aware(value).astimezone(TAIPEI)


def format_date(value: datetime | date | None, fallback: str = "") -> str:
    """格式化為 YYYY-MM-DD（本地時區）。"""
    if value is None:
        return fallback
    if isinstance(value, datetime):
        local = to_local(value)
        return local.strftime("%Y-%m-%d") if local else fallback
    return value.strftime("%Y-%m-%d")


def format_datetime(value: datetime | None, fallback: str = "") -> str:
    """格式化為 YYYY-MM-DD HH:MM（本地時區）。"""
    local = to_local(value)
    return local.strftime("%Y-%m-%d %H:%M") if local else fallback


def iso_date(value: datetime | date | None) -> str | None:
    """ISO-8601 日期字串，供 JSON-LD datePublished 使用。

    JSON-LD 的日期使用「日期」而非完整時間戳，
    因為出版日期的精度本來就是天（SAI §12.2）。
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        local = to_local(value)
        return local.date().isoformat() if local else None
    return value.isoformat()


def iso_datetime(value: datetime | None) -> str | None:
    """完整 ISO-8601 字串（含時區偏移），供 sitemap <lastmod> 使用。

    sitemap 的 lastmod 接受 W3C Datetime 格式；
    帶時區偏移的完整形式是最不容易被誤解的表示法。
    """
    local = to_local(value)
    return local.isoformat() if local else None
