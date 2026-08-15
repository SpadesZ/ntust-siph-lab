# 內建靜態圖片資產

這裡放的是「隨程式碼一起交付、不由管理者上傳」的圖片。

與 `uploads/`（管理者上傳的人物照、成果圖）的差別：

| | `app/static/img/` | `uploads/` |
| --- | --- | --- |
| 來源 | 隨 repo 交付 | 管理者於後台上傳 |
| 儲存 | container image 內 | local 檔案系統 / Cloud Storage |
| 上傳限制 | 不適用（可信內建 asset，SAI §16） | 副檔名 allowlist + Pillow 解碼 |
| Cloud Run | 一律可用 | 需 `STORAGE_BACKEND=gcs` |

因為校徽在 image 內，即使 Cloud Storage 尚未設定，頁首與 favicon
仍會正常顯示。

## 檔案

| 檔案 | 用途 | 尺寸 |
| --- | --- | --- |
| `ntust-emblem-source.webp` | 原始校徽（僅供重新產生衍生檔，不直接引用） | 1280×1281 |
| `ntust-emblem-80.webp` | 頁首 1x | 80×80 |
| `ntust-emblem-160.webp` | 頁首 2x（retina） | 160×160 |
| `favicon.png` | 瀏覽器分頁圖示 | 64×64 |

## 重新產生衍生檔

```python
from PIL import Image
src = Image.open("app/static/img/ntust-emblem-source.webp")
for size, name in [(80, "ntust-emblem-80.webp"), (160, "ntust-emblem-160.webp")]:
    im = src.copy()
    im.thumbnail((size, size), Image.Resampling.LANCZOS)
    im.save(f"app/static/img/{name}", format="WEBP", quality=92, method=6)
fav = src.copy(); fav.thumbnail((64, 64), Image.Resampling.LANCZOS)
fav.save("app/static/img/favicon.png", format="PNG", optimize=True)
```

## 使用規範

校徽屬校方識別資產。使用方式（顏色、留白、變形、與其他標誌並置）
須符合學校的視覺識別規範；不得自行改色或加效果。
