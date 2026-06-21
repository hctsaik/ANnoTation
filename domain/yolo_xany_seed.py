"""Seed X-AnyLabeling sibling JSON from a YOLO dataset folder.

Why this exists
---------------
The annotation workbench (module_012 / X-AnyLabeling) shows existing boxes ONLY
when a per-image ``<image>.json`` (LabelMe / X-AnyLabeling format) sits next to
the image. But a YOLO subset handed over from LV (Export / Send-to-Labeling)
ships boxes as ``labels/<stem>.txt`` plus a ``classes.txt`` — never the sibling
JSON. Result: the data-source step ingests the *images* only, X-AnyLabeling opens
them with no annotations, and the user sees "Labeling 讀不到 Object 標記".

This module is the missing converter: given a YOLO-layout folder it writes, for
each image, a sibling ``<image>.json`` whose ``shapes[].label`` are the real
class NAMES (resolved through ``classes.txt`` / ``data.yaml``), so the boxes —
and their correct class names — show up immediately in the workbench.

Pure stdlib + PIL (image size only); no Streamlit, fully unit-testable.

Contract
--------
* Never clobbers an existing ``<image>.json`` unless ``overwrite=True`` (the user
  may have already annotated; resumed work must win).
* Class id → name uses ``classes.txt`` (one per line); an id past the list keeps
  the numeric id as the label and is reported, never silently dropped.
* Returns a ledger (:class:`SeedReport`) so callers can surface what happened.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

_IMG_EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff")
_XANY_VERSION = "2.4.0"


@dataclass
class SeedReport:
    classes: list[str] = field(default_factory=list)
    images: int = 0          # images considered
    seeded: int = 0          # sibling .json written
    skipped_exist: int = 0   # .json already present (not clobbered)
    no_label: int = 0        # image had no matching labels/*.txt
    boxes: int = 0           # total boxes written
    warnings: list[str] = field(default_factory=list)
    images_dir: str = ""
    labels_dir: str = ""

    def to_dict(self) -> dict:
        return {
            "classes": self.classes, "images": self.images, "seeded": self.seeded,
            "skipped_exist": self.skipped_exist, "no_label": self.no_label,
            "boxes": self.boxes, "warnings": self.warnings,
            "images_dir": self.images_dir, "labels_dir": self.labels_dir,
        }


def read_classes(folder: Path) -> list[str]:
    """Class names from ``<folder>/classes.txt``, falling back to the ``names:``
    list in ``<folder>/data.yaml``. Returns [] if neither yields names."""
    cf = folder / "classes.txt"
    if cf.exists():
        names = [ln.strip() for ln in cf.read_text(encoding="utf-8").splitlines()
                 if ln.strip()]
        if names:
            return names
    dy = folder / "data.yaml"
    if dy.exists():
        return _names_from_data_yaml(dy.read_text(encoding="utf-8"))
    return []


def _names_from_data_yaml(text: str) -> list[str]:
    """Tiny, dependency-free YOLO ``data.yaml`` ``names`` parser. Handles both
    the inline flow list ``names: [a, b]`` and the block list form
    ``names:\\n  - a\\n  - b``. Not a general YAML parser — just enough."""
    # inline: names: ['a', 'b', ...]
    m = re.search(r"^\s*names\s*:\s*\[(.*?)\]", text, re.MULTILINE | re.DOTALL)
    if m:
        return [s.strip().strip("'\"") for s in m.group(1).split(",") if s.strip()]
    # block list under names:
    out: list[str] = []
    in_block = False
    for raw in text.splitlines():
        if re.match(r"^\s*names\s*:\s*$", raw):
            in_block = True
            continue
        if in_block:
            mm = re.match(r"^\s*-\s*(.+?)\s*$", raw)
            if mm:
                out.append(mm.group(1).strip().strip("'\""))
            elif raw.strip() and not raw.startswith((" ", "\t")):
                break  # dedented to a new top-level key
    return out


def _image_size(path: Path) -> tuple[int, int]:
    from PIL import Image
    with Image.open(path) as im:
        return im.size  # (w, h)


def _parse_yolo_txt(text: str) -> tuple[list[tuple[int, float, float, float, float]], list[str]]:
    rows: list[tuple[int, float, float, float, float]] = []
    warns: list[str] = []
    for ln, raw in enumerate(text.splitlines()):
        s = raw.strip()
        if not s:
            continue
        parts = s.split()
        if len(parts) < 5:
            warns.append(f"line {ln}: expected >=5 cols, got {len(parts)}")
            continue
        try:
            cls = int(float(parts[0]))
            cx, cy, w, h = (float(x) for x in parts[1:5])
        except ValueError:
            warns.append(f"line {ln}: non-numeric ({s!r})")
            continue
        rows.append((cls, cx, cy, w, h))
    return rows, warns


def _yolo_to_rect(cx: float, cy: float, w: float, h: float, iw: int, ih: int
                  ) -> list[list[float]]:
    """YOLO normalized [cx,cy,w,h] → clamped pixel rectangle [[x1,y1],[x2,y2]]."""
    x1 = max(0.0, (cx - w / 2) * iw)
    y1 = max(0.0, (cy - h / 2) * ih)
    x2 = min(float(iw), (cx + w / 2) * iw)
    y2 = min(float(ih), (cy + h / 2) * ih)
    return [[round(x1, 2), round(y1, 2)], [round(x2, 2), round(y2, 2)]]


def _build_xany_json(image_name: str, iw: int, ih: int, shapes: list[dict]) -> dict:
    return {
        "version": _XANY_VERSION,
        "flags": {},
        "shapes": shapes,
        "imagePath": image_name,
        "imageData": None,
        "imageHeight": ih,
        "imageWidth": iw,
    }


def _shape(label: str, rect: list[list[float]]) -> dict:
    return {
        "label": label,
        "score": None,
        "points": rect,
        "group_id": None,
        "description": "",
        "difficult": False,
        "shape_type": "rectangle",
        "flags": {},
        "attributes": {},
    }


def _resolve_dirs(folder: Path) -> tuple[Path, Path]:
    """(images_dir, labels_dir). Supports the LV export layout
    (``<folder>/images`` + ``<folder>/labels``) and a flat folder."""
    images_dir = folder / "images" if (folder / "images").is_dir() else folder
    labels_dir = folder / "labels" if (folder / "labels").is_dir() else folder
    return images_dir, labels_dir


def looks_like_yolo_dir(folder: Path) -> bool:
    """True when ``folder`` carries YOLO boxes worth seeding: a labels/ dir (or
    loose .txt, possibly nested under a split subfolder) AND a class source
    (classes.txt / data.yaml)."""
    folder = Path(folder)
    _, labels_dir = _resolve_dirs(folder)
    has_labels = labels_dir.is_dir() and any(labels_dir.rglob("*.txt"))
    has_classes = (folder / "classes.txt").exists() or (folder / "data.yaml").exists()
    return bool(has_labels and has_classes)


def seed_xany_json_from_yolo(folder: Path | str, *, overwrite: bool = False
                             ) -> SeedReport:
    """Write a sibling ``<image>.json`` (X-AnyLabeling format) next to every
    image in ``folder`` whose YOLO ``labels/<stem>.txt`` exists. See module
    docstring for the contract."""
    folder = Path(folder)
    rep = SeedReport()
    classes = read_classes(folder)
    rep.classes = classes
    images_dir, labels_dir = _resolve_dirs(folder)
    rep.images_dir, rep.labels_dir = str(images_dir), str(labels_dir)
    if not images_dir.is_dir():
        rep.warnings.append(f"no images dir: {images_dir}")
        return rep

    # Recurse: YOLO subsets with a split write images/<split>/<img> and
    # labels/<split>/<img>.txt (e.g. LV Export of a "test" split). Mirror each
    # image's path under labels_dir to find its .txt; write the sidecar next to
    # the image (so it works for both flat and split layouts).
    images = sorted(p for p in images_dir.rglob("*")
                    if p.is_file() and p.suffix.lower() in _IMG_EXTS)
    for img in images:
        rep.images += 1
        sidecar = img.with_suffix(".json")
        if sidecar.exists() and not overwrite:
            rep.skipped_exist += 1
            continue
        rel = img.relative_to(images_dir)
        lbl = (labels_dir / rel).with_suffix(".txt")
        if not lbl.exists():
            rep.no_label += 1
            continue
        try:
            iw, ih = _image_size(img)
        except Exception as exc:  # noqa: BLE001 — report, don't crash the batch
            rep.warnings.append(f"{img.name}: cannot read size: {exc}")
            continue
        rows, warns = _parse_yolo_txt(lbl.read_text(encoding="utf-8"))
        for w in warns:
            rep.warnings.append(f"{lbl.name}: {w}")
        shapes = []
        for cls, cx, cy, bw, bh in rows:
            name = classes[cls] if 0 <= cls < len(classes) else str(cls)
            if not (0 <= cls < len(classes)):
                rep.warnings.append(
                    f"{lbl.name}: class id {cls} >= {len(classes)} names; "
                    f"label kept as '{name}'")
            shapes.append(_shape(name, _yolo_to_rect(cx, cy, bw, bh, iw, ih)))
        sidecar.write_text(
            json.dumps(_build_xany_json(img.name, iw, ih, shapes),
                       ensure_ascii=False, indent=2),
            encoding="utf-8")
        rep.seeded += 1
        rep.boxes += len(shapes)
    return rep
