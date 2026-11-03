import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AnnotationCanvas } from "./AnnotationCanvas";
import type { Annotation } from "./types";

const context = {
  clearRect: vi.fn(),
  drawImage: vi.fn(),
  beginPath: vi.fn(),
  moveTo: vi.fn(),
  lineTo: vi.fn(),
  closePath: vi.fn(),
  stroke: vi.fn(),
  fillRect: vi.fn(),
  strokeRect: vi.fn(),
  fillText: vi.fn(),
  measureText: vi.fn(() => ({ width: 16 })),
} as unknown as CanvasRenderingContext2D;

class MockImage {
  naturalWidth = 100;
  naturalHeight = 80;
  onload: (() => void) | null = null;
  set src(_value: string) {
    queueMicrotask(() => this.onload?.());
  }
}

class MockPointerEvent extends MouseEvent {
  pointerId: number;
  constructor(type: string, init: PointerEventInit = {}) {
    super(type, init);
    this.pointerId = init.pointerId ?? 1;
  }
}

function setup(annotation: Annotation, onChange = vi.fn()) {
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(context);
  Object.defineProperty(HTMLCanvasElement.prototype, "setPointerCapture", {
    value: vi.fn(),
    configurable: true,
  });
  vi.stubGlobal("Image", MockImage);
  vi.stubGlobal("PointerEvent", MockPointerEvent);
  render(
    <AnnotationCanvas
      itemKey="image-1"
      imageUrl="blob:test"
      annotation={annotation}
      labels={["scratch", "dent"]}
      onChange={onChange}
    />,
  );
  return onChange;
}

function canvasBounds(canvas: HTMLCanvasElement) {
  Object.defineProperty(canvas, "getBoundingClientRect", {
    value: () => ({ left: 0, top: 0, right: 100, bottom: 80, width: 100, height: 80, x: 0, y: 0, toJSON: () => ({}) }),
  });
}

describe("AnnotationCanvas", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("draws and undoes a rectangle without discarding a polygon", async () => {
    const polygon = { label: "region", shape_type: "polygon", points: [[1, 1], [8, 1], [8, 8]] };
    const onChange = setup({ version: "2.4.0", shapes: [polygon] });
    const canvas = await screen.findByLabelText("圖片與標注框") as HTMLCanvasElement;
    canvasBounds(canvas);
    expect(canvas).toHaveAttribute("width", "100");
    expect(canvas).toHaveAttribute("height", "80");

    fireEvent.pointerDown(canvas, { pointerId: 1, clientX: 10, clientY: 10 });
    fireEvent.pointerMove(canvas, { pointerId: 1, clientX: 50, clientY: 40 });
    fireEvent.pointerUp(canvas, { pointerId: 1, clientX: 50, clientY: 40 });

    await waitFor(() => expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({
      shapes: expect.arrayContaining([expect.objectContaining({ shape_type: "rectangle", label: "scratch", points: [[10, 10], [50, 40]] }), polygon]),
    })));
    fireEvent.click(screen.getByRole("button", { name: "↶ 復原" }));
    await waitFor(() => expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ shapes: [polygon] })));
  });

  it("moves and resizes the selected rectangle", async () => {
    const onChange = setup({
      imageWidth: 100,
      imageHeight: 80,
      shapes: [{ label: "scratch", shape_type: "rectangle", points: [[10, 10], [40, 40]] }],
    });
    const canvas = await screen.findByLabelText("圖片與標注框") as HTMLCanvasElement;
    canvasBounds(canvas);

    fireEvent.pointerDown(canvas, { pointerId: 1, clientX: 20, clientY: 20 });
    fireEvent.pointerMove(canvas, { pointerId: 1, clientX: 25, clientY: 25 });
    fireEvent.pointerUp(canvas, { pointerId: 1, clientX: 25, clientY: 25 });
    await waitFor(() => expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({
      shapes: [expect.objectContaining({ points: [[15, 15], [45, 45]] })],
    })));

    fireEvent.pointerDown(canvas, { pointerId: 2, clientX: 45, clientY: 45 });
    fireEvent.pointerMove(canvas, { pointerId: 2, clientX: 55, clientY: 55 });
    fireEvent.pointerUp(canvas, { pointerId: 2, clientX: 55, clientY: 55 });
    await waitFor(() => expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({
      shapes: [expect.objectContaining({ points: [[15, 15], [55, 55]] })],
    })));
  });
});
