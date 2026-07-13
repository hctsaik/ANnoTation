from __future__ import annotations

import importlib.util
from pathlib import Path


def _load():
    path = Path(__file__).with_name("_canvas_editor.py")
    spec = importlib.util.spec_from_file_location("_canvas_editor_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_standalone_sidecar_conversion_needs_no_platform_plugins() -> None:
    module = _load()
    sidecar = module._canvas_boxes_to_sidecar(
        [{"label": "scratch", "x": -2, "y": 3, "w": 12, "h": 8, "score": None}],
        "frame.jpg",
        100,
        80,
    )

    assert sidecar["imagePath"] == "frame.jpg"
    assert sidecar["shapes"][0]["points"] == [[0.0, 3.0], [10.0, 11.0]]
    assert module._sidecar_to_canvas_boxes(sidecar) == [
        {"label": "scratch", "x": 0.0, "y": 3.0, "w": 10.0, "h": 8.0, "score": None}
    ]


def test_standalone_conversion_preserves_non_rectangle_shapes() -> None:
    module = _load()
    polygon = {"label": "mask", "shape_type": "polygon", "points": [[1, 1], [2, 2]]}
    sidecar = module._canvas_boxes_to_sidecar([], "frame.jpg", 10, 10, keep_shapes=[polygon])

    assert sidecar["shapes"] == [polygon]
