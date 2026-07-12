from __future__ import annotations

import ast
from pathlib import Path

import yaml


_HERE = Path(__file__).parent


def test_annotation_app_declares_complete_product_stages():
    tree = ast.parse((_HERE / "012_runner.py").read_text(encoding="utf-8"))
    assignment = next(
        node for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "_STAGES" for target in node.targets)
    )
    stages = ast.literal_eval(assignment.value)

    assert [stage[0] for stage in stages] == [
        "overview",
        "data",
        "workspace",
        "labels",
        "review",
        "export",
    ]
    assert {stage[2] for stage in stages} == {"015", "026|010", "012", "017", "018", "014"}


def test_annotation_app_uses_single_page_runner():
    manifest = yaml.safe_load((_HERE / "plugin.yaml").read_text(encoding="utf-8"))

    assert manifest["runner"] == "annotation_runner"
    assert manifest["name"] == "Annotation App"
    assert manifest["version"] == "2.0.0"
