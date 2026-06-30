"""Format + LOCATION autodetection for the YOLO→X-AnyLabeling seeder.

Companion to ``test_yolo_xany_seed_roundtrip.py`` (which pins pixel fidelity /
no-clobber / numeric fallback for an *in-folder* class source). This file pins
the new capability the user asked for: "the program must DETECT the format and
the LOCATION and seed to the right place" — find the class source when it is NOT
in the selected folder (a sibling export or the dataset root), detect YOLO by
content, validate it, and refuse rather than mislabel.

The real failure these guard against: selecting ``…/[Small]/test`` (images/ +
labels/ but no classes.txt) while the class list lives at ``[Small]/yolo_pred/
classes.txt`` (sibling) or ``indoor/data.yaml`` (grandparent). The bracketed
``[Small]`` dir name also proves discovery never feeds the path to glob/fnmatch.

A discovered SIBLING is only accepted when it itself carries YOLO boxes — so the
fixtures give each class-source sibling its own ``images/``+``labels/`` (which is
exactly how a real ``yolo_pred`` export looks).
"""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from plugins.labeling.domain.yolo_xany_seed import (
    _max_label_id,
    _names_from_data_yaml,
    find_class_source,
    looks_like_yolo_dir,
    seed_xany_json_from_yolo,
)

_NAMES10 = [f"c{i}" for i in range(10)]  # c0..c9


# ── builders ─────────────────────────────────────────────────────────────────

def _img(path: Path, w: int = 200, h: int = 120) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (w, h), (10, 20, 30)).save(path)


def _txt(path: Path, text: str, *, bom: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8-sig" if bom else "utf-8")


def _classes(path: Path, names: list[str], *, bom: bool = False) -> None:
    _txt(path, "\n".join(names) + "\n", bom=bom)


def _split(root: Path, stem: str = "a", line: str = "3 0.5 0.5 0.25 0.5",
           *, bom: bool = False) -> None:
    """A YOLO split under ``root``: images/<stem>.png + labels/<stem>.txt."""
    _img(root / "images" / f"{stem}.png")
    _txt(root / "labels" / f"{stem}.txt", line + "\n", bom=bom)


def _label(sidecar_dir: Path, stem: str = "a") -> dict:
    return json.loads((sidecar_dir / f"{stem}.json").read_text(encoding="utf-8"))


# ── discovery: beyond the selected folder ────────────────────────────────────

def test_discover_sibling_classes_when_folder_has_none(tmp_path):
    small = tmp_path / "[Small]"            # literal brackets — must not glob
    _split(small / "test")                  # id 3, no classes.txt in test/
    (small / "valid").mkdir(parents=True)   # siblings without a class source
    (small / "yolo").mkdir()
    _split(small / "yolo_pred")             # the class-source sibling IS yolo data
    _classes(small / "yolo_pred" / "classes.txt", _NAMES10)

    rep = seed_xany_json_from_yolo(small / "test")

    assert rep.seeded == 1 and rep.boxes == 1
    assert rep.classes_source.replace("\\", "/").endswith("yolo_pred/classes.txt")
    assert rep.classes_source_kind == "classes.txt"
    assert rep.class_max_id == 3
    assert _label(small / "test" / "images")["shapes"][0]["label"] == "c3"  # real name
    assert any("OUTSIDE the selected folder" in w for w in rep.warnings)


def test_grandparent_used_only_when_no_nearer(tmp_path):
    indoor = tmp_path / "indoor"
    _txt(indoor / "data.yaml", "names: [" + ", ".join(_NAMES10) + "]\n")
    _split(indoor / "[Small]" / "test")     # no classes anywhere in [Small]

    rep = seed_xany_json_from_yolo(indoor / "[Small]" / "test")

    assert rep.seeded == 1
    assert rep.classes_source.replace("\\", "/").endswith("indoor/data.yaml")
    assert rep.classes_source_kind == "data.yaml"
    assert _label(indoor / "[Small]" / "test" / "images")["shapes"][0]["label"] == "c3"


def test_nearest_sibling_beats_farther_grandparent(tmp_path):
    indoor = tmp_path / "indoor"
    _classes(indoor / "classes.txt", [f"g{i}" for i in range(10)])      # grandparent
    _txt(indoor / "data.yaml", "names: [" + ", ".join(f"g{i}" for i in range(10)) + "]\n")
    small = indoor / "[Small]"
    _split(small / "test")
    _split(small / "yolo_pred")
    _classes(small / "yolo_pred" / "classes.txt", [f"s{i}" for i in range(10)])  # sibling

    rep = seed_xany_json_from_yolo(small / "test")

    # the SIBLING (level 1) wins; grandparent (level 2) is never examined
    assert rep.classes_source.replace("\\", "/").endswith("yolo_pred/classes.txt")
    assert _label(small / "test" / "images")["shapes"][0]["label"] == "s3"


def test_climb_past_too_small_nearest_to_valid_ancestor(tmp_path):
    # nearest sibling's class list is too small for the labels → don't refuse at
    # the nearest level; climb to the correctly-sized grandparent source.
    indoor = tmp_path / "indoor"
    _txt(indoor / "data.yaml", "names: [" + ", ".join(_NAMES10) + "]\n")  # 10, correct
    small = indoor / "[Small]"
    _split(small / "test", line="9 0.5 0.5 0.2 0.2")                      # id 9
    _split(small / "yolo_pred")
    _classes(small / "yolo_pred" / "classes.txt", ["too", "small"])       # 2 < 10

    rep = seed_xany_json_from_yolo(small / "test")

    assert rep.classes_source.replace("\\", "/").endswith("indoor/data.yaml")
    assert _label(small / "test" / "images")["shapes"][0]["label"] == "c9"


# ── safety: refuse / ignore rather than mislabel ─────────────────────────────

def test_unrelated_sibling_too_small_is_refused_not_mislabeled(tmp_path):
    small = tmp_path / "[Small]"
    _split(small / "test", line="9 0.5 0.5 0.2 0.2")            # id 9
    _split(small / "yolo_pred")
    _classes(small / "yolo_pred" / "classes.txt", ["only", "three", "names"])  # 3 < 10

    rep = seed_xany_json_from_yolo(small / "test")

    assert rep.classes_source == "" and rep.classes_source_kind == ""
    assert rep.class_max_id == 9
    assert rep.seeded == 1  # boxes still written…
    assert _label(small / "test" / "images")["shapes"][0]["label"] == "9"  # …but numeric
    assert any("max label id" in w for w in rep.warnings)


def test_unrelated_sibling_without_labels_not_grabbed(tmp_path):
    # a neighbour dir that merely holds a classes.txt but is NOT yolo data must
    # never be used (over-grab guard).
    ds = tmp_path / "ds"
    _split(ds / "test")
    _classes(ds / "random_proj" / "classes.txt", _NAMES10)  # no images/labels

    rep = seed_xany_json_from_yolo(ds / "test")

    assert rep.classes_source == ""
    assert _label(ds / "test" / "images")["shapes"][0]["label"] == "3"  # numeric


def test_ambiguous_disagreeing_siblings_refuse(tmp_path):
    ds = tmp_path / "ds"
    _split(ds / "test", line="1 0.5 0.5 0.2 0.2")
    _split(ds / "predA"); _classes(ds / "predA" / "classes.txt", [f"a{i}" for i in range(5)])
    _split(ds / "predB"); _classes(ds / "predB" / "classes.txt", [f"b{i}" for i in range(5)])

    rep = seed_xany_json_from_yolo(ds / "test")

    assert rep.classes_source == ""
    assert _label(ds / "test" / "images")["shapes"][0]["label"] == "1"
    assert any("ambiguous" in w for w in rep.warnings)


def test_ambiguous_message_names_a_genuinely_distinct_pair(tmp_path):
    # predA/predB are identical, predC disagrees → the message must point at the
    # real conflict (predC), not two identical files.
    ds = tmp_path / "ds"
    _split(ds / "test", line="1 0.5 0.5 0.2 0.2")
    _split(ds / "predA"); _classes(ds / "predA" / "classes.txt", [f"m{i}" for i in range(5)])
    _split(ds / "predB"); _classes(ds / "predB" / "classes.txt", [f"m{i}" for i in range(5)])
    _split(ds / "predC"); _classes(ds / "predC" / "classes.txt", [f"n{i}" for i in range(5)])

    rep = seed_xany_json_from_yolo(ds / "test")

    amb = [w for w in rep.warnings if "ambiguous" in w]
    assert amb and "predC" in amb[0]  # the disagreeing source is named


def test_dataset_related_guard_stops_walk(tmp_path):
    # class source 2 levels up, but the intervening grandparent is GENERIC
    # (no data.yaml, no images/labels child, <2 yolo children) → must not grab it.
    generic = tmp_path / "Downloads"
    _classes(generic / "classes.txt", _NAMES10)
    _split(generic / "mid" / "test")

    rep = seed_xany_json_from_yolo(generic / "mid" / "test")

    assert rep.classes_source == ""  # guard stopped the upward walk
    assert _label(generic / "mid" / "test" / "images")["shapes"][0]["label"] == "3"
    assert any("no class source found" in w for w in rep.warnings)


# ── content-based format detection + encoding robustness ─────────────────────

def test_looks_like_yolo_dir_true_without_class_source(tmp_path):
    _split(tmp_path / "test")
    assert looks_like_yolo_dir(tmp_path / "test") is True


def test_dir_with_nonyolo_txt_not_detected(tmp_path):
    d = tmp_path / "notes"
    _img(d / "images" / "a.png")
    _txt(d / "labels" / "a.txt", "this is prose, not a yolo label\n")
    _txt(d / "labels" / "b.txt", "0 640 480 120 90\n")  # pixel coords > 1
    assert looks_like_yolo_dir(d) is False


def test_bom_labels_and_classes_detected_and_seeded(tmp_path):
    # Windows tools often save .txt with a UTF-8 BOM — must not break detection.
    ds = tmp_path / "ds"
    _split(ds / "test", bom=True)
    _classes(ds / "test" / "classes.txt", _NAMES10, bom=True)
    assert looks_like_yolo_dir(ds / "test") is True
    rep = seed_xany_json_from_yolo(ds / "test")
    assert rep.seeded == 1
    assert _label(ds / "test" / "images")["shapes"][0]["label"] == "c3"  # BOM not in name


# ── data.yaml shapes ─────────────────────────────────────────────────────────

def test_data_yaml_name_forms_all_parse():
    assert _names_from_data_yaml("names: [a, b]\n") == ["a", "b"]
    assert _names_from_data_yaml("names:\n  - a\n  - b\n") == ["a", "b"]
    assert _names_from_data_yaml("names: {0: person, 1: car}\n") == ["person", "car"]
    assert _names_from_data_yaml("names:\n  0: person\n  1: car\n") == ["person", "car"]
    # gap filled with numeric id so id→name indexing stays correct
    assert _names_from_data_yaml("names:\n  0: a\n  2: c\n") == ["a", "1", "c"]


def test_seed_with_block_dict_data_yaml(tmp_path):
    indoor = tmp_path / "indoor"
    _txt(indoor / "data.yaml",
         "names:\n" + "".join(f"  {i}: c{i}\n" for i in range(10)))  # block-map form
    _split(indoor / "ds" / "test")

    rep = seed_xany_json_from_yolo(indoor / "ds" / "test")
    assert rep.classes_source.replace("\\", "/").endswith("indoor/data.yaml")
    assert _label(indoor / "ds" / "test" / "images")["shapes"][0]["label"] == "c3"


# ── robustness: one bad file must not abort the batch ────────────────────────

def test_one_bad_label_does_not_abort_batch(tmp_path):
    root = tmp_path / "ds"
    _img(root / "images" / "a.png")
    _img(root / "images" / "b.png")
    _txt(root / "labels" / "a.txt", "0 0.5 0.5 0.2 0.2\n")
    (root / "labels" / "b.txt").mkdir(parents=True)   # a DIR where a file is expected
    _classes(root / "classes.txt", _NAMES10)

    rep = seed_xany_json_from_yolo(root)

    assert rep.images == 2 and rep.seeded == 1
    assert any("seed failed" in w for w in rep.warnings)
    assert (root / "images" / "a.json").exists()
    assert not (root / "images" / "b.json").exists()


# ── in-folder precedence + report shape ──────────────────────────────────────

def test_in_folder_source_wins_over_sibling(tmp_path):
    ds = tmp_path / "ds"
    _split(ds / "test")
    _classes(ds / "test" / "classes.txt", _NAMES10)                  # in-folder
    _split(ds / "yolo_pred")
    _classes(ds / "yolo_pred" / "classes.txt", [f"x{i}" for i in range(10)])  # sibling

    src = find_class_source(ds / "test")
    assert src.in_folder is True
    assert src.path.replace("\\", "/").endswith("test/classes.txt")

    rep = seed_xany_json_from_yolo(ds / "test")
    assert _label(ds / "test" / "images")["shapes"][0]["label"] == "c3"  # in-folder names
    assert not any("OUTSIDE" in w for w in rep.warnings)


def test_report_records_source_fields_in_to_dict(tmp_path):
    _split(tmp_path / "test")
    _classes(tmp_path / "test" / "classes.txt", _NAMES10)
    d = seed_xany_json_from_yolo(tmp_path / "test").to_dict()
    assert set(d) >= {"classes_source", "classes_source_kind", "class_max_id"}
    assert d["classes_source"].replace("\\", "/").endswith("test/classes.txt")
    assert d["classes_source_kind"] == "classes.txt"
    assert d["class_max_id"] == 3


# ── small unit helpers ───────────────────────────────────────────────────────

def test_max_label_id(tmp_path):
    labels = tmp_path / "labels"
    _txt(labels / "a.txt", "0 0.5 0.5 0.2 0.2\n9 0.4 0.4 0.1 0.1\n")
    _txt(labels / "b.txt", "5 0.5 0.5 0.2 0.2\n")
    _classes(labels / "classes.txt", _NAMES10)  # must be ignored by the scan
    assert _max_label_id(labels) == 9
    assert _max_label_id(tmp_path / "empty") == -1


def test_brackets_in_sibling_dir_name_no_glob(tmp_path):
    # a class source nested in a literally-bracketed dir is found via iterdir,
    # never expanded as an fnmatch char-class.
    root = tmp_path / "ds"
    _split(root / "test")
    _split(root / "[pred v2]")
    _classes(root / "[pred v2]" / "classes.txt", _NAMES10)
    src = find_class_source(root / "test")
    assert src.path.replace("\\", "/").endswith("[pred v2]/classes.txt")
    assert src.names == _NAMES10
