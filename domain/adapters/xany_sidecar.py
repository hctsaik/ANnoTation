"""X-AnyLabeling 2.4.0 sidecar 信封的**單一真相來源**。

工作台（module_012 / X-AnyLabeling）只在影像旁有 ``<image>.json`` 時才顯示既有框。
有兩個寫出這個 sidecar 的地方——``yolo_xany_seed``（YOLO→sidecar）與
``web_labeling``（web→sidecar）。把信封與 shape 集中在這裡，未來 X-AnyLabeling
版本一升（2.4.0→…）或欄位變動只改一處，避免兩份副本悄悄漂移（format-drift =
false-green）。純 stdlib。
"""
from __future__ import annotations

XANY_VERSION = "2.4.0"
IMG_EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff")


def xany_shape(label: str, points: list[list[float]], shape_type: str = "rectangle") -> dict:
    """一個 X-AnyLabeling 2.4.0 shape（rectangle = 2 對角點；polygon = 環點）。"""
    return {
        "label": label,
        "score": None,
        "points": points,
        "group_id": None,
        "description": "",
        "difficult": False,
        "shape_type": shape_type,
        "flags": {},
        "attributes": {},
    }


def build_xany_json(image_name: str, iw: int, ih: int, shapes: list[dict]) -> dict:
    """一張影像的 X-AnyLabeling sidecar 信封。"""
    return {
        "version": XANY_VERSION,
        "flags": {},
        "shapes": shapes,
        "imagePath": image_name,
        "imageData": None,
        "imageHeight": ih,
        "imageWidth": iw,
    }
