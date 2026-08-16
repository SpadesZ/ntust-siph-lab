# ADR-014 — `/healthz` 為 Cloud Run 保留路徑，另設 `/health` 供外部監控

> **檔案路徑**：`docs/adr/ADR-014-healthz-reserved-path.md`
> **建立日期**：2026-08-17　**版本**：v1.0
> **狀態**：**鎖定（Accepted）**

---

## 1. 問題：上線後才可能發現的平台限制

SAI §19 與附錄 A 指定健康檢查端點為 `/healthz`，`deploy/cloudrun.md` §10
的部署後檢查清單第一項也是「`/healthz` 回 200」。

2026-08-17 首次部署到 Cloud Run 後，實測發現：

```
$ curl -i https://<service>.run.app/healthz
HTTP/1.1 404 Not Found
Content-Type: text/html; charset=UTF-8
Referrer-Policy: no-referrer

<title>Error 404 (Not Found)!!1</title>   ← Google 的錯誤頁，不是本站的
```

**`/healthz` 是 Cloud Run 的保留路徑**：Google Frontend（GFE）會在請求
抵達容器前攔截並回自己的 404 頁面。

### 如何確認是 GFE 而非應用程式回的 404

這是本案的關鍵鑑別方法，日後遇到類似症狀可重複使用：

| 觀察點 | `/healthz`（GFE 回應） | `/__nope__`（應用程式回應） |
| --- | --- | --- |
| 頁面內容 | Google 制式錯誤頁 | 本站中文 404 頁 |
| `Content-Security-Policy` | **無** | 有 |
| `X-Frame-Options` / HSTS | **無** | 有 |
| `Referrer-Policy` | `no-referrer` | `strict-origin-when-cross-origin` |
| Cloud Run request log | **完全沒有紀錄** | 有 404 紀錄 |

「應用程式的安全標頭全部消失」＋「request log 查無此請求」＝
請求根本沒進到容器。

---

## 2. 影響範圍（比表面看起來小）

關鍵在於**只有經過 GFE 的外部請求會被攔截**：

| 使用情境 | 是否經過 GFE | `/healthz` 是否可用 |
| --- | --- | --- |
| Cloud Run startup / liveness probe | ❌ 直連容器 | ✅ 可用 |
| Dockerfile `HEALTHCHECK` | ❌ 容器內 | ✅ 可用 |
| 本機 `docker compose` | ❌ 無 GFE | ✅ 可用 |
| `pytest` | ❌ Flask test client | ✅ 可用 |
| **外部 uptime 監控** | ✅ | ❌ **不可用** |
| **`scripts/smoke_cloud.py`** | ✅ | ❌ **不可用** |

因此這個問題「本機開發、容器測試、CI 全部看不出來」——
與 ADR 記錄的 `check_same_thread` 事件（見 `app/config.py`
的 `engine_options_for` 說明）屬於同一類：**只有實跑正式平台才會暴露**。

---

## 3. 裁示

| 項目 | 內容 |
| --- | --- |
| **決定** | 保留 `/healthz`，另外新增 `/health` 作為同一 handler 的別名 |
| 核准人 | 研究室管理者（本專案委託人） |
| 核准日期 | **2026-08-17** |
| **委託人原話** | 於選項中選擇「新增 /health 別名路由」 |

> 第 4、5 節為審查者（實作者）補充的工程說明，非委託人陳述。

### 為什麼是「新增別名」而不是「改名」（審查者補充）

改名會破壞三個仍然正常運作的契約：

1. SAI §19 與附錄 A 明文指定 `/healthz`（規格為外部交付，不應為平台細節改寫）。
2. `Dockerfile` 的 `HEALTHCHECK` 在容器內運作正常。
3. `deploy/service.yaml` 的 `startupProbe` / `livenessProbe` 直連容器，運作正常。

別名的成本是一行 decorator，且不影響上述任何一項。

---

## 4. 實作

`app/blueprints/public/routes.py`：

```python
@public_bp.route("/healthz")
@public_bp.route("/health")
def healthz():
    ...
```

兩條路徑共用同一個 handler，回應完全相同
（`tests/test_health.py::test_health_alias_matches_healthz` 固定此行為）。

兩者都不進 sitemap。

---

## 5. 後續責任

- **外部監控一律使用 `/health`**。若日後接 Cloud Monitoring uptime check
  或第三方監控，設定的路徑必須是 `/health`。
- `scripts/smoke_cloud.py` 的健康檢查項目已改指向 `/health`。
- 若日後改用 global external Application Load Balancer 掛自訂網域，
  應重新確認 `/healthz` 是否仍被攔截 —— 攔截行為來自 `run.app` 的 GFE，
  自訂網域路徑未必相同。屆時 `/health` 仍然可用，不需再改。
- **禁止**以「重複路由」為由移除 `/health`：本機與容器內測試都不會失敗，
  但正式環境的外部監控會靜默失效。`tests/test_health.py` 已加註此風險。

---

## 6. 相關

- 規格來源：SAI §19、附錄 A（Route Matrix）
- 實作：`app/blueprints/public/routes.py`
- 測試：`tests/test_health.py`
- 部署文件：`deploy/cloudrun.md` §10
- 同類事件（只有實跑正式環境才會暴露）：`app/config.py` `engine_options_for()`
