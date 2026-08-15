# ============================================================
# NTUST SiPh Lab - Post-Deployment Smoke Test
#
# 上下游：
#   Cloud Run revision URL（或任何已部署站台）
#       -> 本腳本（純 HTTP 黑箱檢查）
#       -> 非零 exit code -> 部署流程中止 / traffic 不切換
#
# 檔案路徑：
#   scripts/smoke_cloud.py
#
# 建立日期：2026-08-15
# 最後重大修改：2026-08-15
# 版本：v1.0
#
# 模組定位與責任邊界：
#   SAI §21.4 明定「每次 deployment 後執行 /healthz、首頁、人物頁、
#   成果頁與 Admin login smoke test」。§21.6 進一步要求先以
#   revision URL 做 smoke test，通過後才切 100% traffic。
#   本腳本就是那個 gate。
#
#   責任邊界（不得做的事）：
#     - 不得連線資料庫（這是黑箱檢查，只用 HTTP）。
#     - 不得需要管理員憑證（不做登入，只確認登入頁與保護行為）。
#     - 不得修改任何資料（全部是 GET；唯一的 POST 是無 token 的
#       CSRF 拒絕測試，預期被擋下，不會產生任何寫入）。
#
# 輸入 -> 處理 -> 輸出 Pipeline：
#   base URL
#     -> 逐項檢查（健康、公開頁、SEO、安全、Admin 保護）
#     -> 逐行輸出 PASS/FAIL
#     -> 任一 FAIL -> exit code 1
#
# 主要 Function：
#   run_smoke(base_url) - 執行全部檢查並回傳結果
#
# 依賴套件：
#   requests
#
# 環境變數：
#   SMOKE_BASE_URL - 目標站台（亦可用位置參數）
#
# 資料庫使用方式：無。
#
# Error Handling / Fallback：
#   連線失敗視為 FAIL 而非例外中止，讓報告能完整列出所有項目 ——
#   部署當下需要一次看到全貌，而不是修一個跑一次。
#
# 特殊機制（為什麼檢查 HTTPS 與 cookie flags）：
#   正式環境的 SESSION_COOKIE_SECURE 必須為 true（SAI §11.1）。
#   這個設定錯誤在本機完全看不出來，只有在真實 HTTPS 站台上
#   才能驗證，因此必須放在部署後的 smoke test 而非單元測試。
#
# 特殊機制（為什麼檢查 canonical 主機）：
#   PUBLIC_BASE_URL 若忘了改成正式網域，canonical 與 sitemap
#   會指向 localhost 或 run.app，直接傷害 SEO（SAI §12.1、
#   附錄 C「PUBLIC_BASE_URL、canonical、sitemap 使用正式網域」）。
#   這是 cutover 最常見的疏漏，且不會造成任何錯誤畫面 ——
#   只會安靜地讓索引指向錯誤位置。
#
# 已知限制與禁止事項：
#   1. 不做登入後的 CRUD 驗證（需要憑證，屬人工 cutover 檢查）。
#   2. 禁止把此腳本當成效能測試。
#
# 維護契約：
#   新增公開路由時必須加入 PUBLIC_PATHS，否則新頁面在部署後
#   不會被檢查到。
#
# 驗證方式：
#   python scripts/smoke_cloud.py https://siph-lab.ntust.edu.tw
#   python scripts/smoke_cloud.py http://127.0.0.1:8000 --allow-insecure
# ============================================================

from __future__ import annotations

import argparse
import os
import re
import sys
from urllib.parse import urlparse

try:
    import requests
except ImportError:  # pragma: no cover
    print("需要 requests：pip install requests", file=sys.stderr)
    raise SystemExit(1)

#: 必須回 200 的公開頁面（SAI 附錄 A Route Matrix）。
PUBLIC_PATHS = ("/", "/about", "/members", "/research", "/alumni", "/join")

#: 必須存在的安全 headers（SAI §11、[S8]）。
REQUIRED_HEADERS = (
    "Content-Security-Policy",
    "X-Content-Type-Options",
    "X-Frame-Options",
    "Referrer-Policy",
)


class Result:
    """收集檢查結果並決定 exit code。"""

    def __init__(self) -> None:
        self.rows: list[tuple[str, bool, str]] = []

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.rows.append((name, ok, detail))
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))

    @property
    def failed(self) -> int:
        return sum(1 for _, ok, _ in self.rows if not ok)


def run_smoke(base_url: str, allow_insecure: bool = False) -> Result:
    base = base_url.rstrip("/")
    result = Result()
    session = requests.Session()
    session.headers["User-Agent"] = "ntust-siph-lab-smoke/1.0"

    parsed = urlparse(base)
    is_https = parsed.scheme == "https"

    # --- 1. 健康檢查（SAI §19、附錄 A）---
    try:
        r = session.get(f"{base}/healthz", timeout=20)
        result.add("/healthz 回應 200", r.status_code == 200, f"HTTP {r.status_code}")
    except Exception as exc:  # noqa: BLE001
        result.add("/healthz 回應 200", False, f"連線失敗：{exc}")
        return result  # 服務不可用時後續檢查沒有意義

    # --- 2. 公開頁面 ---
    for path in PUBLIC_PATHS:
        try:
            r = session.get(f"{base}{path}", timeout=20)
            result.add(f"公開頁 {path}", r.status_code == 200, f"HTTP {r.status_code}")
        except Exception as exc:  # noqa: BLE001
            result.add(f"公開頁 {path}", False, str(exc))

    # --- 3. SEO 基礎（SAI §12.1）---
    home = session.get(f"{base}/", timeout=20)
    html = home.text

    canonical = re.search(r'<link rel="canonical" href="([^"]+)"', html)
    result.add("首頁有 canonical", bool(canonical),
               canonical.group(1) if canonical else "缺少")

    if canonical:
        canon_host = urlparse(canonical.group(1)).netloc
        # 見檔頭「為什麼檢查 canonical 主機」。
        result.add(
            "canonical 指向本站網域（PUBLIC_BASE_URL 已正確設定）",
            canon_host == parsed.netloc,
            f"canonical={canon_host}、實際={parsed.netloc}",
        )

    result.add("首頁有 meta description", 'name="description"' in html)
    result.add("首頁恰好一個 H1", len(re.findall(r"<h1\b", html)) == 1,
               f"{len(re.findall(r'<h1\b', html))} 個")

    for path, needle, label in (
        ("/robots.txt", "Disallow: /admin", "robots.txt 禁止 /admin"),
        ("/sitemap.xml", "<urlset", "sitemap.xml 格式正確"),
    ):
        r = session.get(f"{base}{path}", timeout=20)
        result.add(label, r.status_code == 200 and needle in r.text,
                   f"HTTP {r.status_code}")

    sitemap = session.get(f"{base}/sitemap.xml", timeout=20).text
    locs = re.findall(r"<loc>([^<]+)</loc>", sitemap)
    bad_locs = [l for l in locs if urlparse(l).netloc != parsed.netloc]
    result.add("sitemap 全部指向本站網域", not bad_locs,
               f"{len(locs)} 筆" + (f"，異常：{bad_locs[:3]}" if bad_locs else ""))

    # --- 4. 安全 headers ---
    for header in REQUIRED_HEADERS:
        result.add(f"security header {header}", header in home.headers,
                   home.headers.get(header, "缺少")[:60])

    if is_https:
        result.add("HSTS 已啟用", "Strict-Transport-Security" in home.headers,
                   home.headers.get("Strict-Transport-Security", "缺少"))

    # --- 5. Admin 保護（SAI §7.1、AC-01）---
    r = session.get(f"{base}/admin", allow_redirects=False, timeout=20)
    location = r.headers.get("Location", "")
    result.add("未登入 /admin 導向登入頁（AC-01）",
               r.status_code in (301, 302) and "/admin/login" in location,
               f"HTTP {r.status_code} -> {location}")

    login = session.get(f"{base}/admin/login", timeout=20)
    result.add("/admin/login 可存取", login.status_code == 200, f"HTTP {login.status_code}")
    result.add("登入表單含 CSRF token", 'name="csrf_token"' in login.text)

    # 無 token 的 POST 必須被拒（SAI §11.2）。不會產生任何寫入。
    r = requests.post(f"{base}/admin/login", data={"username": "smoke", "password": "smoke"},
                      timeout=20)
    result.add("缺 CSRF token 的登入被拒（400/403）", r.status_code in (400, 403),
               f"HTTP {r.status_code}")

    # --- 6. Cookie 安全旗標（見檔頭特殊機制）---
    if is_https:
        cookie = next((c for c in login.cookies if c.name == "siph_session"), None)
        if cookie is not None:
            result.add("session cookie 有 Secure 旗標", bool(cookie.secure),
                       f"secure={cookie.secure}")
    elif not allow_insecure:
        result.add("目標使用 HTTPS", False,
                   "正式站台必須是 HTTPS（SAI §11.1）；本機測試請加 --allow-insecure")

    # --- 7. 404 行為（SAI §19、AC-19）---
    r = session.get(f"{base}/__smoke_missing__", timeout=20)
    result.add("未知路徑回 404 且無 stack trace",
               r.status_code == 404 and "Traceback" not in r.text,
               f"HTTP {r.status_code}")

    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Cloud Run 部署後煙霧測試（SAI §21.4、§21.6）。"
    )
    parser.add_argument("base_url", nargs="?", default=os.environ.get("SMOKE_BASE_URL"),
                        help="目標站台，例如 https://siph-lab.ntust.edu.tw")
    parser.add_argument("--allow-insecure", action="store_true",
                        help="允許 http:// 目標（僅供本機驗證）。")
    args = parser.parse_args(argv)

    if not args.base_url:
        print("需要目標 URL（位置參數或 SMOKE_BASE_URL）。", file=sys.stderr)
        return 1

    print(f"Smoke test：{args.base_url}\n")
    result = run_smoke(args.base_url, allow_insecure=args.allow_insecure)

    total = len(result.rows)
    print(f"\n結果：{total - result.failed}/{total} 通過")
    if result.failed:
        print("\n部署未通過 smoke test（SAI §21.6：不得切換 traffic）。")
        return 1
    print("\n全部通過，可依 §21.6 繼續切換 traffic。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
