"""Test bootstrap for the labeling plugin's own test suite.

Labeling is a white-box plugin: its tests import ``plugins.labeling.domain.*``
(and the platform contract via ``core``), so they run with the host engine on
sys.path. When labeling is checked out as a submodule under a nativeApp tree,
``plugins/labeling/tests/`` -> ``parents[3]`` is ``sidecar/python-engine`` (the
host engine root), so inserting it makes ``plugins.labeling`` / ``core`` import.

Run them via the platform suite (``npm run test:python`` covers both
``tests/`` and ``plugins/labeling/tests/``) or directly:
    python -m pytest sidecar/python-engine/plugins/labeling/tests
"""
from __future__ import annotations

import sys
from pathlib import Path

_ENGINE_ROOT = Path(__file__).parents[3]
if str(_ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(_ENGINE_ROOT))
