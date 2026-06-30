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

Format + location autodetection
-------------------------------
A user may point at a *split* folder (``…/test``) that carries ``images/`` +
``labels/`` but NO ``classes.txt`` — the class list lives at the dataset root
(``…/data.yaml``) or in a sibling export (``…/yolo_pred/classes.txt``). So:

* YOLO detection is **content-based** (every inspected line = ``int id`` + ``4
  floats in [0,1]``), not "a ``.txt`` exists" — arbitrary text never triggers it.
* The class source is **discovered beyond the selected folder**: in-folder first
  (authoritative), then the nearest ancestor level — that ancestor's own
  ``classes.txt``/``data.yaml`` then its immediate child dirs (siblings of the
  selected folder, each gated on actually carrying YOLO boxes so an unrelated
  neighbour's class list is never grabbed). The walk is bounded (``max_up``), a
  dataset-related guard stops it escaping into a generic parent, and a max-id
  check refuses a class list too small to name the labels rather than mislabel.
  The chosen source is recorded in :class:`SeedReport` and a beyond-folder pick
  is flagged LOUDLY so a wrong same-shaped ``classes.txt`` is visible, not silent.

Pure stdlib + PIL (image size only); no Streamlit, fully unit-testable.

Contract
--------
* Never clobbers an existing ``<image>.json`` unless ``overwrite=True`` (the user
  may have already annotated; resumed work must win).
* Class id → name uses the discovered class source; an id past the list keeps
  the numeric id as the label and is reported, never silently dropped.
* One unreadable image / label / failed write only skips that image (warned),
  never aborts the batch.
* Returns a ledger (:class:`SeedReport`) so callers can surface what happened —
  including WHICH class source was used (``classes_source``).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from plugins.labeling.domain.adapters.xany_sidecar import (
    IMG_EXTS as _IMG_EXTS,
    build_xany_json as _build_xany_json,
    xany_shape,
)

# Dirs never worth scanning for a class source (junk / huge / unrelated).
_NOISE_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv"}
# .txt files in a labels dir that are NOT per-image YOLO label files.
_LABEL_NOISE_NAMES = {"classes.txt", "notes.txt"}
# Prefer the unambiguous id→name list over data.yaml when both sit together.
_KIND_RANK = {"classes.txt": 0, "data.yaml": 1}


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
    classes_source: str = ""       # abs path of the chosen class file ('' if none)
    classes_source_kind: str = ""  # 'classes.txt' | 'data.yaml' | ''
    class_max_id: int = -1         # max class id seen in the labels (-1 if none)

    def to_dict(self) -> dict:
        return {
            "classes": self.classes, "images": self.images, "seeded": self.seeded,
            "skipped_exist": self.skipped_exist, "no_label": self.no_label,
            "boxes": self.boxes, "warnings": self.warnings,
            "images_dir": self.images_dir, "labels_dir": self.labels_dir,
            "classes_source": self.classes_source,
            "classes_source_kind": self.classes_source_kind,
            "class_max_id": self.class_max_id,
        }


@dataclass
class ClassSource:
    """Result of :func:`find_class_source`. ``names`` empty = none usable."""
    names: list[str] = field(default_factory=list)
    path: str = ""
    kind: str = ""            # 'classes.txt' | 'data.yaml' | ''
    in_folder: bool = False   # True when found in/under the selected folder
    candidates: list = field(default_factory=list)  # (abs_path, kind, name_count)
    warnings: list[str] = field(default_factory=list)


# ── class-source reading ──────────────────────────────────────────────────────

def _ordered_from_pairs(pairs: list[str]) -> list[str]:
    """``['0: a', '1: b']`` (any order) → ``['a', 'b']`` ordered by int key. A gap
    is filled with the numeric id so indexing a name by class id stays correct."""
    mapping: dict[int, str] = {}
    for p in pairs:
        if ":" not in p:
            continue
        k, _, v = p.partition(":")
        k, v = k.strip().strip("'\""), v.strip().strip("'\"")
        if not k or not v:
            continue
        try:
            mapping[int(k)] = v
        except ValueError:
            continue
    if not mapping:
        return []
    return [mapping.get(i, str(i)) for i in range(max(mapping) + 1)]


def _names_from_data_yaml(text: str) -> list[str]:
    """Tiny, dependency-free YOLO ``data.yaml`` ``names`` parser. Handles the four
    real-world shapes — inline list ``names: [a, b]``, inline map
    ``names: {0: a, 1: b}``, block list ``names:\\n  - a``, and block map
    ``names:\\n  0: a`` (the form modern Ultralytics emits). Not a general YAML
    parser — just enough."""
    # inline flow list: names: ['a', 'b', ...]
    m = re.search(r"^\s*names\s*:\s*\[(.*?)\]", text, re.MULTILINE | re.DOTALL)
    if m:
        return [s.strip().strip("'\"") for s in m.group(1).split(",") if s.strip()]
    # inline flow map: names: {0: a, 1: b}
    m = re.search(r"^\s*names\s*:\s*\{(.*?)\}", text, re.MULTILINE | re.DOTALL)
    if m:
        return _ordered_from_pairs(m.group(1).split(","))
    # block forms under `names:` — collect both list items and int-keyed map rows
    list_items: list[str] = []
    map_pairs: list[str] = []
    in_block = False
    for raw in text.splitlines():
        if re.match(r"^\s*names\s*:\s*$", raw):
            in_block = True
            continue
        if in_block:
            mlist = re.match(r"^\s*-\s*(.+?)\s*$", raw)
            mmap = re.match(r"^\s*(\d+)\s*:\s*(.+?)\s*$", raw)
            if mlist:
                list_items.append(mlist.group(1).strip().strip("'\""))
            elif mmap:
                map_pairs.append(f"{mmap.group(1)}: {mmap.group(2)}")
            elif raw.strip() and not raw.startswith((" ", "\t")):
                break  # dedented to a new top-level key
    if list_items:
        return list_items
    return _ordered_from_pairs(map_pairs)


def _classes_from_dir(d: Path) -> tuple[list[str], str]:
    """Read ONE directory → (names, kind). ``classes.txt`` (one name per line)
    wins over ``data.yaml``. ``utf-8-sig`` so a Windows BOM never corrupts the
    first name. Returns ``([], "")`` if neither yields names or on any read error
    (never raises — discovery must not abort on one bad file)."""
    try:
        cf = d / "classes.txt"
        if cf.is_file():
            names = [ln.strip() for ln in cf.read_text(encoding="utf-8-sig").splitlines()
                     if ln.strip()]
            if names:
                return names, "classes.txt"
        dy = d / "data.yaml"
        if dy.is_file():
            names = _names_from_data_yaml(dy.read_text(encoding="utf-8-sig"))
            if names:
                return names, "data.yaml"
    except (OSError, UnicodeDecodeError):
        return [], ""
    return [], ""


# ── content-based YOLO detection + label stats ────────────────────────────────

def _is_yolo_label_file(path: Path) -> bool:
    """True when ``path`` reads as a YOLO label file: ≥1 box line and NO line that
    contradicts the format (checked across the WHOLE file, no early break). A box
    line = ``int class id`` + ``≥4 floats`` with the first four in ``[0,1]`` (small
    epsilon for rounding). Rejects prose, COCO-json-as-txt, and pixel-coord boxes
    (values > 1). ``utf-8-sig`` tolerates a leading BOM."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return False
    valid = 0
    for raw in text.splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        parts = s.split()
        if len(parts) < 5:
            return False
        try:
            cid = int(float(parts[0]))
            box = [float(x) for x in parts[1:5]]
        except ValueError:
            return False
        if cid < 0 or not all(-0.001 <= v <= 1.001 for v in box):
            return False
        valid += 1
    return valid >= 1


def _label_txts(labels_dir: Path) -> list[Path]:
    """All per-image ``.txt`` under ``labels_dir`` (excludes classes.txt/notes.txt).
    rglob over a literal base — the base may contain ``[brackets]``; only the
    bracket-free ``*.txt`` pattern is interpreted, so it is matched as a plain
    name, never a glob char-class."""
    try:
        return sorted(
            (p for p in labels_dir.rglob("*.txt")
             if p.is_file() and p.name.casefold() not in _LABEL_NOISE_NAMES),
            key=lambda p: str(p).casefold())
    except OSError:
        return []


def _dir_has_yolo_boxes(labels_dir: Path, *, max_files: int = 8) -> bool:
    """True iff ``labels_dir`` carries content-verified YOLO boxes. Samples the
    first ``max_files`` label files; if none qualify (e.g. all empty), widens to
    the rest once before deciding False."""
    if not labels_dir.is_dir():
        return False
    txts = _label_txts(labels_dir)
    if not txts:
        return False
    if any(_is_yolo_label_file(p) for p in txts[:max_files]):
        return True
    return any(_is_yolo_label_file(p) for p in txts[max_files:])


def _max_label_id(labels_dir: Path) -> int:
    """Max integer class id across ALL YOLO labels under ``labels_dir`` (-1 if
    none). Full scan (cheap — first token per line only) so the validation that a
    discovered class list covers every id is exact, not a lower bound."""
    if not labels_dir.is_dir():
        return -1
    mx = -1
    for p in _label_txts(labels_dir):
        try:
            text = p.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError):
            continue
        for raw in text.splitlines():
            s = raw.strip()
            if not s or s.startswith("#"):
                continue
            try:
                cid = int(float(s.split()[0]))
            except (ValueError, IndexError):
                continue
            if cid > mx:
                mx = cid
    return mx


# ── class-source discovery (the new "find the class file anywhere sane") ──────

def _sorted_child_dirs(base: Path) -> list[Path]:
    """Immediate child directories of ``base``, casefold-name sorted, via
    ``iterdir`` (literal — safe for ``[bracketed]`` names). [] on error."""
    try:
        kids = [c for c in base.iterdir() if c.is_dir()]
    except OSError:
        return []
    return sorted(kids, key=lambda c: c.name.casefold())


def _looks_dataset_related(p: Path) -> bool:
    """Gate the upward walk above the immediate parent: only climb into ``p`` if
    it plausibly belongs to THIS dataset — it has a ``data.yaml`` (a YOLO root
    descriptor), an ``images``/``labels`` child, or ≥2 child dirs that
    content-look-like YOLO splits. Keyed on STRUCTURE, not on a bare
    ``classes.txt`` (a generic parent — Desktop/Downloads/… — may hold an
    unrelated one we must never grab)."""
    try:
        if (p / "data.yaml").is_file():
            return True
    except OSError:
        return False
    children = _sorted_child_dirs(p)
    names = {c.name.casefold() for c in children}
    if "images" in names or "labels" in names:
        return True
    yolo_children = 0
    for c in children:
        if c.name.casefold() in _NOISE_DIRS:
            continue
        lbl = c / "labels" if (c / "labels").is_dir() else c
        if _dir_has_yolo_boxes(lbl, max_files=4):
            yolo_children += 1
            if yolo_children >= 2:
                return True
    return False


def find_class_source(folder: Path, labels_dir: Path | None = None, *,
                      max_up: int = 3, max_id: int | None = None) -> ClassSource:
    """Locate the YOLO class source for ``folder``, searching beyond it when the
    selected folder has none. Deterministic; see module docstring for the policy.

    Phase A (authoritative, never validation-refused): the selected folder, then
    its ``labels/`` dir. Phase B (beyond the folder): climb one ancestor level at
    a time (≤ ``max_up``, gated by :func:`_looks_dataset_related` above the
    immediate parent). At each level consider the ancestor's OWN class file plus
    each immediate child dir that itself carries YOLO boxes (so an unrelated
    neighbour is never grabbed). A level's candidates must cover ``max_id``: if
    all are too small the search KEEPS CLIMBING for a correctly-sized source; if
    two correctly-sized lists DISAGREE it refuses. A beyond-folder pick is flagged.
    """
    folder = Path(folder).resolve()
    if labels_dir is None:
        _, labels_dir = _resolve_dirs(folder)
    labels_dir = Path(labels_dir)
    mx = _max_label_id(labels_dir) if max_id is None else max_id

    # ── Phase A — in/under the selected folder (authoritative) ────────────────
    names, kind = _classes_from_dir(folder)
    if names:
        return ClassSource(names, str(folder / kind), kind, in_folder=True)
    if labels_dir != folder:
        names, kind = _classes_from_dir(labels_dir)
        if names:
            return ClassSource(names, str(labels_dir / kind), kind, in_folder=True)

    # ── Phase B — beyond the selected folder, nearest valid level wins ────────
    parents = folder.parents
    came_from = folder
    rejected: list = []  # too-small candidates seen while climbing (for the report)
    for i in range(1, max_up + 1):
        if i - 1 >= len(parents):
            break
        base = parents[i - 1]
        if base == came_from:
            break
        if i >= 2 and not _looks_dataset_related(base):
            break  # don't escape into an unrelated parent

        cands: list[tuple[list[str], str, str]] = []  # (names, abs_path, kind)
        n, k = _classes_from_dir(base)  # ancestor's OWN file: trusted, no gate
        if n:
            cands.append((n, str(base / k), k))
        for child in _sorted_child_dirs(base):
            if child == came_from or child.name.casefold() in _NOISE_DIRS:
                continue
            if not _dir_has_yolo_boxes(child):
                continue  # gate: only a sibling that is itself YOLO data
            cn, ck = _classes_from_dir(child)
            if cn:
                cands.append((cn, str(child / ck), ck))

        if not cands:
            came_from = base
            continue  # nothing at this level — climb one more

        cands.sort(key=lambda t: (_KIND_RANK.get(t[2], 9), t[1].casefold()))
        report = [(p, kk, len(nm)) for (nm, p, kk) in cands]
        valid = [c for c in cands if len(c[0]) > mx]
        if not valid:
            # every candidate here is too small to name these labels — wrong
            # source. Remember it and keep climbing for a correctly-sized one.
            rejected.extend(report)
            came_from = base
            continue
        # distinct correctly-sized name lists (preserve sort order) = genuine clash
        reps: list[tuple[list[str], str, str]] = []
        seen: set = set()
        for c in valid:
            key = tuple(c[0])
            if key not in seen:
                seen.add(key)
                reps.append(c)
        if len(reps) > 1:
            return ClassSource(
                [], "", "", in_folder=False, candidates=report,
                warnings=[f"ambiguous class sources disagree: {reps[0][1]} vs "
                          f"{reps[1][1]}; refusing to guess — drop a classes.txt "
                          f"into the selected folder. boxes labeled by numeric id"])
        win = valid[0]
        warns = [f"class names taken from OUTSIDE the selected folder: {win[1]} "
                 f"(kind={win[2]}); verify these labels match this dataset"]
        if len(valid) > 1:
            warns.append(f"{len(valid)} equivalent class sources found, used {win[1]}")
        return ClassSource(win[0], win[1], win[2], in_folder=False,
                           candidates=report, warnings=warns)

    if rejected:
        return ClassSource(
            [], "", "", in_folder=False, candidates=rejected,
            warnings=[f"class source rejected: every candidate's class count "
                      f"<= max label id ({mx}); boxes labeled by numeric id "
                      f"(candidates: {rejected})"])
    return ClassSource(
        [], "", "", in_folder=False, candidates=[],
        warnings=[f"no class source found within {max_up} levels up / sibling "
                  f"folders; boxes labeled by numeric id"])


def read_classes(folder: Path) -> list[str]:
    """Back-compat shim — class names for ``folder``. Now delegates to
    :func:`find_class_source` (in-folder first, then nearest ancestor/sibling),
    so it resolves names in strictly more cases than before. [] if none."""
    return find_class_source(Path(folder)).names


# ── YOLO geometry / layout ────────────────────────────────────────────────────

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


def _resolve_dirs(folder: Path) -> tuple[Path, Path]:
    """(images_dir, labels_dir). Supports the LV export layout
    (``<folder>/images`` + ``<folder>/labels``) and a flat folder."""
    images_dir = folder / "images" if (folder / "images").is_dir() else folder
    labels_dir = folder / "labels" if (folder / "labels").is_dir() else folder
    return images_dir, labels_dir


def looks_like_yolo_dir(folder: Path) -> bool:
    """True when ``folder`` carries content-verified YOLO boxes (a ``labels/`` dir
    or loose ``.txt`` whose lines parse as YOLO). A class source is NO LONGER
    required here — it is discovered separately (:func:`find_class_source`), so a
    split folder without an in-folder ``classes.txt`` still seeds."""
    folder = Path(folder).resolve()
    _, labels_dir = _resolve_dirs(folder)
    return _dir_has_yolo_boxes(labels_dir)


def seed_xany_json_from_yolo(folder: Path | str, *, overwrite: bool = False,
                             max_up: int = 3) -> SeedReport:
    """Write a sibling ``<image>.json`` (X-AnyLabeling format) next to every
    image in ``folder`` whose YOLO ``labels/<stem>.txt`` exists. The class source
    is auto-located (in-folder → nearest ancestor/sibling). See module docstring
    for the contract."""
    folder = Path(folder).resolve()
    rep = SeedReport()
    images_dir, labels_dir = _resolve_dirs(folder)
    rep.images_dir, rep.labels_dir = str(images_dir), str(labels_dir)
    rep.class_max_id = _max_label_id(labels_dir)

    src = find_class_source(folder, labels_dir, max_up=max_up, max_id=rep.class_max_id)
    classes = src.names
    rep.classes = classes
    rep.classes_source = src.path
    rep.classes_source_kind = src.kind
    rep.warnings.extend(src.warnings)

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
        # One unreadable image / label / failed write skips only this image.
        try:
            iw, ih = _image_size(img)
            rows, warns = _parse_yolo_txt(lbl.read_text(encoding="utf-8-sig"))
            for w in warns:
                rep.warnings.append(f"{lbl.name}: {w}")
            shapes = []
            for cls, cx, cy, bw, bh in rows:
                name = classes[cls] if 0 <= cls < len(classes) else str(cls)
                if not (0 <= cls < len(classes)):
                    rep.warnings.append(
                        f"{lbl.name}: class id {cls} >= {len(classes)} names; "
                        f"label kept as '{name}'")
                shapes.append(xany_shape(name, _yolo_to_rect(cx, cy, bw, bh, iw, ih), "rectangle"))
            sidecar.write_text(
                json.dumps(_build_xany_json(img.name, iw, ih, shapes),
                           ensure_ascii=False, indent=2),
                encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 — report, don't crash the batch
            rep.warnings.append(f"{img.name}: seed failed: {exc}")
            continue
        rep.seeded += 1
        rep.boxes += len(shapes)
    return rep
