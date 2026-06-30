"""_img_data_url 驗收:大圖編碼上限 + (path,mtime) 快取;座標空間必須維持原圖尺寸。"""
from __future__ import annotations

import base64
import importlib.util as _u
import io
from pathlib import Path

from PIL import Image

# _canvas_editor 模組級只 import base64/json/os/pathlib(streamlit 延後到函式內),可直接載入。
_CE_PATH = Path(__file__).resolve().parents[1] / "modules" / "module_012" / "_canvas_editor.py"
_spec = _u.spec_from_file_location("_ce_under_test", _CE_PATH)
_ce = _u.module_from_spec(_spec)
_spec.loader.exec_module(_ce)


def _encoded_size(data_url: str) -> tuple[int, int]:
    raw = base64.b64decode(data_url.split(",", 1)[1])
    with Image.open(io.BytesIO(raw)) as im:
        return im.size


def test_large_image_encode_capped_but_dims_original(tmp_path):
    p = tmp_path / "big.png"
    Image.new("RGB", (5000, 3000), (10, 20, 30)).save(p)
    _ce._DATAURL_CACHE.clear()
    url, w, h = _ce._img_data_url(str(p))
    # 回傳的尺寸 = 原圖(座標空間),不可因編碼縮放而改變
    assert (w, h) == (5000, 3000)
    # 但編碼出來的 raster 長邊被夾到 <= _MAX_ENCODE_DIM
    ew, eh = _encoded_size(url)
    assert max(ew, eh) <= _ce._MAX_ENCODE_DIM
    assert min(ew, eh) > 0


def test_small_image_not_upscaled(tmp_path):
    p = tmp_path / "small.png"
    Image.new("RGB", (320, 200), (1, 2, 3)).save(p)
    _ce._DATAURL_CACHE.clear()
    url, w, h = _ce._img_data_url(str(p))
    assert (w, h) == (320, 200)
    assert _encoded_size(url) == (320, 200)   # 不放大


def test_cache_hit_returns_same_object(tmp_path):
    p = tmp_path / "c.png"
    Image.new("RGB", (640, 480), (4, 5, 6)).save(p)
    _ce._DATAURL_CACHE.clear()
    a = _ce._img_data_url(str(p))
    b = _ce._img_data_url(str(p))
    assert a is b                              # (path,mtime) 命中 → 同一物件,未重編
