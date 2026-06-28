"""谷底契約尖刺：YOLO subset → seeder → X-AnyLabeling sidecar 的「來回保真度」。

這是 web-labeling 動工前的 de-risk 測試（Step 0）。它驗的是**既有**的
``yolo_xany_seed.seed_xany_json_from_yolo``——也就是三條標註路（X-AnyLabeling /
LabelMe / Web）最終都要灌進去的那張嘴。在加任何 Web 模組之前先證明這張嘴對
bbox 是像素級保真、名稱（非數字 id）對齊、且不覆寫人工成果。

刻意用「非正方形」影像（1920×1080）：percent↔pixel 或 w/h 對調的 bug 只在
非方形露餡，正方形會一起過綠（false-green）。
"""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from plugins.labeling.domain.yolo_xany_seed import seed_xany_json_from_yolo

_W, _H = 1920, 1080  # 非正方形：抓 percent↔pixel / w-h 對調


def _make_image(path: Path, w: int = _W, h: int = _H) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (w, h), (10, 20, 30)).save(path)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_seed_roundtrip_nonsquare_pixel_fidelity(tmp_path):
    # 一個已知的 YOLO 框：中心(0.5,0.5)、寬0.25、高0.5
    _make_image(tmp_path / "frame.jpg")
    _write(tmp_path / "frame.txt", "1 0.5 0.5 0.25 0.5\n")
    _write(tmp_path / "classes.txt", "cat\ndog\nbird\n")

    rep = seed_xany_json_from_yolo(tmp_path)

    assert rep.seeded == 1 and rep.boxes == 1
    sidecar = tmp_path / "frame.json"
    assert sidecar.exists()
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    assert data["imageWidth"] == _W and data["imageHeight"] == _H

    shape = data["shapes"][0]
    # 名稱對齊：id=1 → classes[1] == "dog"（不是數字 "1"）
    assert shape["label"] == "dog"
    assert shape["shape_type"] == "rectangle"

    # 像素級保真：期望矩形 [[720,270],[1200,810]]，誤差 <= 1px
    (x1, y1), (x2, y2) = shape["points"]
    expected = [(720.0, 270.0), (1200.0, 810.0)]
    for (gx, gy), (ex, ey) in zip([(x1, y1), (x2, y2)], expected):
        assert abs(gx - ex) <= 1.0, f"x off: {gx} vs {ex}"
        assert abs(gy - ey) <= 1.0, f"y off: {gy} vs {ey}"


def test_seed_no_clobber_preserves_human_work(tmp_path):
    _make_image(tmp_path / "frame.jpg")
    _write(tmp_path / "frame.txt", "0 0.5 0.5 0.2 0.2\n")
    _write(tmp_path / "classes.txt", "cat\n")
    # 人已經改過的 sidecar：再 seed 不准蓋掉（resumed work wins）
    human = {"version": "2.4.0", "shapes": [{"label": "HUMAN-EDIT"}], "_mark": 1}
    (tmp_path / "frame.json").write_text(json.dumps(human), encoding="utf-8")

    rep = seed_xany_json_from_yolo(tmp_path)  # overwrite=False (default)

    assert rep.skipped_exist == 1 and rep.seeded == 0
    after = json.loads((tmp_path / "frame.json").read_text(encoding="utf-8"))
    assert after == human  # 一個位元都沒動

    # overwrite=True 才會覆蓋
    rep2 = seed_xany_json_from_yolo(tmp_path, overwrite=True)
    assert rep2.seeded == 1
    after2 = json.loads((tmp_path / "frame.json").read_text(encoding="utf-8"))
    assert after2["shapes"][0]["label"] == "cat"


def test_seed_unknown_class_id_kept_and_reported(tmp_path):
    # id 超出 classes 範圍：保留數字當 label 並回報，不可靜默丟掉
    _make_image(tmp_path / "frame.jpg")
    _write(tmp_path / "frame.txt", "7 0.5 0.5 0.2 0.2\n")
    _write(tmp_path / "classes.txt", "cat\ndog\n")

    rep = seed_xany_json_from_yolo(tmp_path)

    data = json.loads((tmp_path / "frame.json").read_text(encoding="utf-8"))
    assert data["shapes"][0]["label"] == "7"
    assert any("class id 7" in w for w in rep.warnings)


def test_seed_split_layout_images_labels_subfolders(tmp_path):
    # LV export 的 split 版面：images/ + labels/ + data.yaml（名稱來源）
    _make_image(tmp_path / "images" / "a.jpg")
    _write(tmp_path / "labels" / "a.txt", "0 0.5 0.5 1.0 1.0\n")
    _write(tmp_path / "data.yaml", "names: [thing]\n")

    rep = seed_xany_json_from_yolo(tmp_path)

    assert rep.seeded == 1
    sidecar = tmp_path / "images" / "a.json"   # sidecar 寫在影像旁
    assert sidecar.exists()
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    assert data["shapes"][0]["label"] == "thing"
    # 整張圖的框：clamp 到邊界 [[0,0],[1920,1080]]
    (x1, y1), (x2, y2) = data["shapes"][0]["points"]
    assert (abs(x1) <= 1 and abs(y1) <= 1
            and abs(x2 - _W) <= 1 and abs(y2 - _H) <= 1)
