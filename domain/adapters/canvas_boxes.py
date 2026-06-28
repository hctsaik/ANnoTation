"""Canvas 框 ⇄ X-AnyLabeling sidecar 的契約橋(瀏覽器內標註用)。

工作台 module_012 只認影像旁的 ``<image>.json`` sidecar(X-AnyLabeling 形狀)。
in-browser 編輯器用的是最簡單的像素框 ``{label, x, y, w, h}``(左上角 + 寬高)。
這支兩邊互轉:

* :func:`sidecar_to_canvas_boxes` — 載入既有 sidecar(含 auto-label 預標、2 點或
  4 點 rectangle)→ 給編輯器的像素框清單。
* :func:`canvas_boxes_to_sidecar` — 編輯器存檔的像素框 → X-AnyLabeling sidecar dict
  (clamp 到影像、名稱式 label、用既有 ``xany_sidecar`` 形狀,與 seeder/AI 預標同一份
  契約)。退化框(零/負面積、空 label)丟掉。

純 stdlib;不開圖(尺寸由呼叫端帶入)。寫回是**人按存檔的明確覆寫**(編輯即最新),
由呼叫端決定何時 write_text。
"""
from __future__ import annotations

from plugins.labeling.domain.adapters.xany_sidecar import build_xany_json, xany_shape


def sidecar_to_canvas_boxes(sidecar: dict) -> list[dict]:
    """X-AnyLabeling sidecar dict → 編輯器像素框清單。支援 2 點與 4 點 rectangle。"""
    boxes: list[dict] = []
    for shape in sidecar.get("shapes", []):
        if shape.get("shape_type") != "rectangle":
            continue
        pts = shape.get("points") or []
        if len(pts) < 2:
            continue
        xs = [float(p[0]) for p in pts]
        ys = [float(p[1]) for p in pts]
        x1, y1, x2, y2 = min(xs), min(ys), max(xs), max(ys)
        boxes.append({
            "label": shape.get("label", ""),
            "x": x1, "y": y1, "w": x2 - x1, "h": y2 - y1,
            "score": shape.get("score"),
        })
    return boxes


def canvas_boxes_to_sidecar(
    boxes: list[dict], image_name: str, image_width: int, image_height: int,
) -> dict:
    """編輯器像素框 → X-AnyLabeling sidecar dict。clamp 到影像;退化框/空 label 丟掉。"""
    iw, ih = float(image_width), float(image_height)
    shapes: list[dict] = []
    for b in boxes:
        try:
            x, y = float(b["x"]), float(b["y"])
            w, h = float(b["w"]), float(b["h"])
        except (KeyError, TypeError, ValueError):
            continue
        label = str(b.get("label", "")).strip()
        if not label:
            continue
        # 允許編輯器傳負寬高(往左上拖):用 min/max 正規化,再 clamp 到影像。
        x1 = max(0.0, min(x, x + w))
        y1 = max(0.0, min(y, y + h))
        x2 = min(iw, max(x, x + w))
        y2 = min(ih, max(y, y + h))
        if x2 <= x1 or y2 <= y1:
            continue
        shapes.append(xany_shape(
            label, [[round(x1, 2), round(y1, 2)], [round(x2, 2), round(y2, 2)]],
            "rectangle"))
    return build_xany_json(image_name, int(iw), int(ih), shapes)
