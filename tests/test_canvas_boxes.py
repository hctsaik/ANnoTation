"""canvas_boxes 驗收:in-browser 編輯器像素框 ⇄ X-AnyLabeling sidecar。"""
from __future__ import annotations

from plugins.labeling.domain.adapters.canvas_boxes import (
    canvas_boxes_to_sidecar,
    sidecar_to_canvas_boxes,
)


def test_boxes_to_sidecar_shape_and_clamp():
    boxes = [{"label": "dog", "x": 100, "y": 200, "w": 50, "h": 60}]
    sc = canvas_boxes_to_sidecar(boxes, "frame.jpg", 1920, 1080)
    assert sc["imagePath"] == "frame.jpg"
    assert sc["imageWidth"] == 1920 and sc["imageHeight"] == 1080
    shape = sc["shapes"][0]
    assert shape["label"] == "dog" and shape["shape_type"] == "rectangle"
    assert shape["points"] == [[100.0, 200.0], [150.0, 260.0]]


def test_boxes_negative_drag_normalized():
    # 往左上拖 → 負寬高;正規化成正框
    boxes = [{"label": "dog", "x": 150, "y": 260, "w": -50, "h": -60}]
    sc = canvas_boxes_to_sidecar(boxes, "f.jpg", 1920, 1080)
    assert sc["shapes"][0]["points"] == [[100.0, 200.0], [150.0, 260.0]]


def test_boxes_out_of_bounds_clamped():
    boxes = [{"label": "dog", "x": -30, "y": -10, "w": 5000, "h": 5000}]
    sc = canvas_boxes_to_sidecar(boxes, "f.jpg", 1920, 1080)
    (x1, y1), (x2, y2) = sc["shapes"][0]["points"]
    assert x1 == 0.0 and y1 == 0.0 and x2 == 1920.0 and y2 == 1080.0


def test_boxes_drop_degenerate_and_empty_label():
    boxes = [
        {"label": "dog", "x": 10, "y": 10, "w": 0, "h": 50},     # 零寬 → drop
        {"label": "", "x": 10, "y": 10, "w": 50, "h": 50},        # 空 label → drop
        {"label": "cat", "x": 10, "y": 10, "w": 50, "h": 50},     # keep
    ]
    sc = canvas_boxes_to_sidecar(boxes, "f.jpg", 1920, 1080)
    assert len(sc["shapes"]) == 1 and sc["shapes"][0]["label"] == "cat"


def test_sidecar_to_boxes_handles_2pt_and_4pt():
    # 2 點(LabelMe)與 4 點(X-AnyLabeling / AI 預標 _run_ai_items)都要讀成同一框
    sidecar = {"shapes": [
        {"label": "dog", "shape_type": "rectangle", "points": [[100, 200], [150, 260]]},
        {"label": "cat", "shape_type": "rectangle", "score": 0.9,
         "points": [[10, 20], [60, 20], [60, 80], [10, 80]]},
        {"label": "skip", "shape_type": "polygon", "points": [[0, 0], [1, 1], [2, 0]]},
    ]}
    boxes = sidecar_to_canvas_boxes(sidecar)
    assert len(boxes) == 2  # polygon 略過
    dog = next(b for b in boxes if b["label"] == "dog")
    assert (dog["x"], dog["y"], dog["w"], dog["h"]) == (100.0, 200.0, 50.0, 60.0)
    cat = next(b for b in boxes if b["label"] == "cat")
    assert (cat["x"], cat["y"], cat["w"], cat["h"]) == (10.0, 20.0, 50.0, 60.0)
    assert cat["score"] == 0.9


def test_roundtrip_boxes_sidecar_boxes():
    boxes = [{"label": "dog", "x": 100, "y": 200, "w": 50, "h": 60},
             {"label": "cat", "x": 700, "y": 700, "w": 80, "h": 80}]
    sc = canvas_boxes_to_sidecar(boxes, "f.jpg", 1920, 1080)
    back = sidecar_to_canvas_boxes(sc)
    assert len(back) == 2
    for orig, got in zip(boxes, back):
        assert orig["label"] == got["label"]
        for k in ("x", "y", "w", "h"):
            assert abs(orig[k] - got[k]) <= 0.5
