import { describe, expect, it } from "vitest";
import { boxToRectangle, clampBox, filterItems, rectangleToBox, replaceRectangles, type Box } from "./annotationModel";
import type { Annotation, Shape } from "./types";

describe("annotation geometry and shape preservation", () => {
  it("converts rectangle corners into a normalized editable box", () => {
    const box = rectangleToBox({ shape_type: "rectangle", label: "dent", points: [[18, 22], [4, 6]] });
    expect(box).toEqual({ x: 4, y: 6, width: 14, height: 16, label: "dent", score: undefined });
  });

  it("preserves non-rectangle shapes and their order when rectangles are edited", () => {
    const polygon: Shape = { shape_type: "polygon", label: "region", points: [[0, 0], [5, 0], [5, 5]] };
    const point: Shape = { shape_type: "point", label: "landmark", points: [[2, 3]] };
    const annotation: Annotation = {
      version: "2.4.0",
      shapes: [boxToRectangle({ x: 1, y: 1, width: 5, height: 7, label: "old" }), polygon, point],
    };
    const updated = replaceRectangles(annotation, [{ x: 2, y: 3, width: 6, height: 8, label: "new" }]);
    expect(updated.shapes.map((shape) => shape.shape_type)).toEqual(["rectangle", "polygon", "point"]);
    expect(updated.shapes[1]).toBe(polygon);
    expect(updated.shapes[2]).toBe(point);
    expect(rectangleToBox(updated.shapes[0])).toMatchObject({ x: 2, y: 3, width: 6, height: 8, label: "new" });
  });

  it("appends newly drawn rectangles without changing existing polygon order", () => {
    const polygon: Shape = { shape_type: "polygon", label: "region", points: [[0, 0], [5, 0], [5, 5]] };
    const annotation: Annotation = { shapes: [polygon] };
    expect(replaceRectangles(annotation, [{ x: 1, y: 2, width: 3, height: 4, label: "scratch" }]).shapes[0]).toBe(polygon);
  });

  it("clamps boxes to image dimensions and filters items by status and name", () => {
    const box: Box = { x: -4, y: 8, width: 20, height: 14, label: "scratch" };
    expect(clampBox(box, 10, 12)).toMatchObject({ x: 0, y: 8, width: 10, height: 4 });
    const items = [
      { name: "panel-a.png", has_annotation: true, has_classification: true },
      { name: "panel-b.png", has_annotation: false, has_classification: false },
    ];
    expect(filterItems(items, "PANEL", "pending").map((item) => item.name)).toEqual(["panel-b.png"]);
    expect(filterItems(items, "", "classified").map((item) => item.name)).toEqual(["panel-a.png"]);
    expect(filterItems([
      { name: "low.png", max_conf: 0.34 },
      { name: "high.png", max_conf: 0.78 },
    ], "", "all", "all", "low").map((item) => item.name)).toEqual(["low.png"]);
  });
});
