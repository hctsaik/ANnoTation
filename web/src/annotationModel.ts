import type { Annotation, Shape } from "./types";

export type Box = { x: number; y: number; width: number; height: number; label: string; score?: number | null };

export function rectangleToBox(shape: Shape): Box | null {
  if (shape.shape_type !== "rectangle" || !Array.isArray(shape.points) || shape.points.length < 2) return null;
  const first = shape.points[0];
  const second = shape.points[1];
  if (!first || !second || first.length < 2 || second.length < 2) return null;
  const x = Math.min(Number(first[0]), Number(second[0]));
  const y = Math.min(Number(first[1]), Number(second[1]));
  const x2 = Math.max(Number(first[0]), Number(second[0]));
  const y2 = Math.max(Number(first[1]), Number(second[1]));
  if (![x, y, x2, y2].every(Number.isFinite) || x2 <= x || y2 <= y) return null;
  return { x, y, width: x2 - x, height: y2 - y, label: shape.label, score: shape.score };
}

export function boxToRectangle(box: Box): Shape {
  return {
    label: box.label.trim(),
    score: box.score ?? null,
    points: [[box.x, box.y], [box.x + box.width, box.y + box.height]],
    group_id: null,
    description: "",
    difficult: false,
    shape_type: "rectangle",
    flags: {},
    attributes: {},
  };
}

export function replaceRectangles(annotation: Annotation, boxes: Box[]): Annotation {
  const shapes: Shape[] = [];
  let inserted = false;
  for (const shape of annotation.shapes) {
    if (shape.shape_type === "rectangle") {
      if (!inserted) {
        shapes.push(...boxes.map(boxToRectangle));
        inserted = true;
      }
      continue;
    }
    shapes.push(shape);
  }
  if (!inserted) shapes.push(...boxes.map(boxToRectangle));
  return { ...annotation, shapes };
}

export function filterItems<T extends { name: string; has_annotation?: boolean; has_classification?: boolean; review_status?: string; classification?: string; labels?: string[]; shapes?: Shape[]; max_conf?: number }>(
  items: T[],
  query: string,
  status: string,
  classification = "all",
  confidence = "all",
  label = "all",
): T[] {
  const normalized = query.trim().toLocaleLowerCase();
  return items.filter((item) => {
    const matchesQuery = !normalized || item.name.toLocaleLowerCase().includes(normalized);
    const matchesStatus =
      status === "all" ||
      (status === "annotated" && Boolean(item.has_annotation)) ||
      (status === "pending" && !item.has_annotation) ||
      (status === "classified" && Boolean(item.has_classification)) ||
      (status === "unclassified" && !item.has_classification) ||
      (status === "approved" && item.review_status === "approved") ||
      (status === "rejected" && item.review_status === "rejected") ||
      (status === "review-pending" && (!item.review_status || item.review_status === "pending"));
    const matchesClassification = classification === "all" ||
      (classification === "unclassified" && !item.classification) ||
      (classification !== "unclassified" && item.classification === classification);
    const maxConfidence = item.max_conf ?? Math.max(0, ...(item.shapes ?? []).map((shape) => Number(shape.score) || 0));
    const matchesConfidence = confidence !== "low" || (maxConfidence > 0 && maxConfidence < 0.5);
    const itemLabels = item.labels ?? item.shapes?.map((shape) => shape.label) ?? [];
    const matchesLabel = label === "all" || itemLabels.includes(label);
    return matchesQuery && matchesStatus && matchesClassification && matchesConfidence && matchesLabel;
  });
}

export function clampBox(box: Box, imageWidth: number, imageHeight: number): Box {
  const x = Math.max(0, Math.min(imageWidth, box.x));
  const y = Math.max(0, Math.min(imageHeight, box.y));
  const x2 = Math.max(x, Math.min(imageWidth, box.x + box.width));
  const y2 = Math.max(y, Math.min(imageHeight, box.y + box.height));
  return { ...box, x, y, width: x2 - x, height: y2 - y };
}
