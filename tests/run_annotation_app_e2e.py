"""Fail-fast runner for all Annotation App Playwright suites."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


HERE = Path(__file__).parent
SUITES = (
    "e2e_annotation_app_critical_path.py",
    "e2e_web_annotation_canvas.py",
    "e2e_annotation_app_scenarios.py",
)


def main() -> None:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    for suite in SUITES:
        print(f"\n=== {suite} ===", flush=True)
        subprocess.run([sys.executable, str(HERE / suite)], check=True, env=env)


if __name__ == "__main__":
    main()
