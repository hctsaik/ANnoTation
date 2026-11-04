import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import { clampBox, rectangleToBox, replaceRectangles, type Box } from "./annotationModel";
import type { Annotation } from "./types";

type Props = {
  itemKey: string;
  imageUrl: string;
  annotation: Annotation;
  labels: string[];
  readOnly?: boolean;
  onChange: (value: Annotation) => void;
};

type Gesture = {
  kind: "draw" | "move" | "resize";
  index: number;
  startX: number;
  startY: number;
  original?: Box;
  handle?: ResizeHandle;
  symmetric?: boolean;
  before: Box[];
};

type ResizeHandle = "n" | "ne" | "e" | "se" | "s" | "sw" | "w" | "nw";

const MIN_SIZE = 3;

function resizeBox(
  original: Box,
  handle: ResizeHandle,
  dx: number,
  dy: number,
  imageWidth: number,
  imageHeight: number,
  symmetric = false,
): Box {
  if (symmetric) {
    const centerX = original.x + original.width / 2;
    const centerY = original.y + original.height / 2;
    const maxHalfWidth = Math.max(0, Math.min(centerX, imageWidth - centerX));
    const maxHalfHeight = Math.max(0, Math.min(centerY, imageHeight - centerY));
    const halfWidth = handle.includes("e") || handle.includes("w")
      ? Math.min(maxHalfWidth, Math.max(MIN_SIZE / 2, original.width / 2 + (handle.includes("e") ? dx : -dx)))
      : original.width / 2;
    const halfHeight = handle.includes("n") || handle.includes("s")
      ? Math.min(maxHalfHeight, Math.max(MIN_SIZE / 2, original.height / 2 + (handle.includes("s") ? dy : -dy)))
      : original.height / 2;
    return {
      ...original,
      x: centerX - halfWidth,
      y: centerY - halfHeight,
      width: halfWidth * 2,
      height: halfHeight * 2,
    };
  }
  let left = original.x;
  let right = original.x + original.width;
  let top = original.y;
  let bottom = original.y + original.height;
  if (handle.includes("w")) left = Math.max(0, Math.min(right - MIN_SIZE, original.x + dx));
  if (handle.includes("e")) right = Math.min(imageWidth, Math.max(left + MIN_SIZE, original.x + original.width + dx));
  if (handle.includes("n")) top = Math.max(0, Math.min(bottom - MIN_SIZE, original.y + dy));
  if (handle.includes("s")) bottom = Math.min(imageHeight, Math.max(top + MIN_SIZE, original.y + original.height + dy));
  return { ...original, x: left, y: top, width: right - left, height: bottom - top };
}

export function AnnotationCanvas({ itemKey, imageUrl, annotation, labels, readOnly = false, onChange }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const imageRef = useRef<HTMLImageElement | null>(null);
  const gestureRef = useRef<Gesture | null>(null);
  const undoRef = useRef<Box[][]>([]);
  const redoRef = useRef<Box[][]>([]);
  const [boxes, setBoxes] = useState<Box[]>(() => annotation.shapes.map(rectangleToBox).filter((box): box is Box => box !== null));
  const [selected, setSelected] = useState(-1);
  const [activeLabel, setActiveLabel] = useState(labels[0] ?? "object");
  const [dimensions, setDimensions] = useState({ width: annotation.imageWidth ?? 0, height: annotation.imageHeight ?? 0 });
  const [tool, setTool] = useState<"draw" | "select">("draw");
  const [undoCount, setUndoCount] = useState(0);
  const [redoCount, setRedoCount] = useState(0);

  useEffect(() => {
    const image = new Image();
    image.onload = () => {
      imageRef.current = image;
      setDimensions({ width: image.naturalWidth, height: image.naturalHeight });
      paint();
    };
    image.src = imageUrl;
    return () => {
      imageRef.current = null;
    };
    // A canvas is remounted when the selected image changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [itemKey, imageUrl]);

  useEffect(() => {
    setActiveLabel((current) => (labels.includes(current) ? current : labels[0] ?? "object"));
  }, [labels]);

  useEffect(() => {
    paint();
  }, [boxes, selected, dimensions, annotation]);

  function paint() {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    const image = imageRef.current;
    if (!canvas || !ctx || !image || !dimensions.width || !dimensions.height) return;
    if (canvas.width !== dimensions.width || canvas.height !== dimensions.height) {
      canvas.width = dimensions.width;
      canvas.height = dimensions.height;
    }
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(image, 0, 0, canvas.width, canvas.height);
    for (const shape of annotation.shapes) {
      if (shape.shape_type === "rectangle" || !Array.isArray(shape.points) || shape.points.length < 2) continue;
      ctx.beginPath();
      shape.points.forEach(([x, y], index) => index === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y));
      if (shape.shape_type === "polygon") ctx.closePath();
      ctx.strokeStyle = "#f4a340";
      ctx.lineWidth = Math.max(2, canvas.width / 700);
      ctx.stroke();
    }
    boxes.forEach((box, index) => {
      ctx.lineWidth = Math.max(2, canvas.width / 700);
      ctx.strokeStyle = index === selected ? "#f0643b" : "#2da585";
      ctx.fillStyle = index === selected ? "rgba(240,100,59,.12)" : "rgba(45,165,133,.10)";
      ctx.fillRect(box.x, box.y, box.width, box.height);
      ctx.strokeRect(box.x, box.y, box.width, box.height);
      const fontSize = Math.max(14, canvas.width / 90);
      ctx.font = `600 ${fontSize}px Inter, sans-serif`;
      const caption = `${box.label}${box.score == null ? "" : ` ${(box.score * 100).toFixed(0)}%`}`;
      const captionWidth = ctx.measureText(caption).width + 12;
      ctx.fillStyle = index === selected ? "#f0643b" : "#2da585";
      ctx.fillRect(box.x, Math.max(0, box.y - fontSize - 8), captionWidth, fontSize + 8);
      ctx.fillStyle = "#fff";
      ctx.fillText(caption, box.x + 6, Math.max(fontSize, box.y - 6));
      if (index === selected) {
        const handle = Math.max(8, canvas.width / 130);
        ctx.fillStyle = "#fff";
        ctx.strokeStyle = "#f0643b";
        const handles: Array<[number, number]> = [
          [box.x, box.y], [box.x + box.width / 2, box.y], [box.x + box.width, box.y],
          [box.x + box.width, box.y + box.height / 2], [box.x + box.width, box.y + box.height],
          [box.x + box.width / 2, box.y + box.height], [box.x, box.y + box.height],
          [box.x, box.y + box.height / 2],
        ];
        for (const [x, y] of handles) {
          ctx.fillRect(x - handle / 2, y - handle / 2, handle, handle);
          ctx.strokeRect(x - handle / 2, y - handle / 2, handle, handle);
        }
      }
    });
  }

  function emit(next: Box[]) {
    if (readOnly) return;
    setBoxes(next);
    onChange(replaceRectangles(annotation, next));
  }

  function commit(before: Box[]) {
    undoRef.current = [...undoRef.current.slice(-49), before];
    redoRef.current = [];
    setUndoCount(undoRef.current.length);
    setRedoCount(0);
  }

  function coords(event: PointerEvent<HTMLCanvasElement>) {
    const rect = event.currentTarget.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return { x: 0, y: 0 };
    const scaleX = event.currentTarget.width / rect.width;
    const scaleY = event.currentTarget.height / rect.height;
    return {
      x: Math.max(0, Math.min(event.currentTarget.width, (event.clientX - rect.left) * scaleX)),
      y: Math.max(0, Math.min(event.currentTarget.height, (event.clientY - rect.top) * scaleY)),
    };
  }

  function resizeHandleAt(box: Box, x: number, y: number): ResizeHandle | null {
    const tolerance = Math.max(8, dimensions.width / 260 + 5);
    const midX = box.x + box.width / 2;
    const midY = box.y + box.height / 2;
    const handles: Array<[ResizeHandle, number, number]> = [
      ["nw", box.x, box.y], ["n", midX, box.y], ["ne", box.x + box.width, box.y],
      ["e", box.x + box.width, midY], ["se", box.x + box.width, box.y + box.height],
      ["s", midX, box.y + box.height], ["sw", box.x, box.y + box.height],
      ["w", box.x, midY],
    ];
    const hit = handles
      .map(([name, handleX, handleY]) => ({ name, distance: Math.hypot(x - handleX, y - handleY) }))
      .filter((candidate) => candidate.distance <= tolerance)
      .sort((left, right) => left.distance - right.distance)[0];
    return hit?.name || null;
  }

  function pointerDown(event: PointerEvent<HTMLCanvasElement>) {
    if (readOnly || !dimensions.width || !dimensions.height) return;
    event.currentTarget.focus();
    event.currentTarget.setPointerCapture(event.pointerId);
    const { x, y } = coords(event);
    const selectedBox = boxes[selected];
    const selectedHandle = selectedBox ? resizeHandleAt(selectedBox, x, y) : null;
    if (selectedBox && selectedHandle) {
      gestureRef.current = {
        kind: "resize", index: selected, handle: selectedHandle,
        startX: x, startY: y, original: selectedBox, symmetric: event.altKey, before: boxes,
      };
      return;
    }
    let hit = -1;
    for (let index = boxes.length - 1; index >= 0; index -= 1) {
      const box = boxes[index];
      if (x >= box.x && x <= box.x + box.width && y >= box.y && y <= box.y + box.height) {
        hit = index;
        break;
      }
    }
    const current = hit >= 0 ? boxes[hit] : undefined;
    if (current) {
      setSelected(hit);
      gestureRef.current = { kind: "move", index: hit, startX: x, startY: y, original: current, before: boxes };
    } else if (tool === "draw") {
      const next = [...boxes, { x, y, width: 0, height: 0, label: activeLabel }];
      setSelected(next.length - 1);
      gestureRef.current = { kind: "draw", index: next.length - 1, startX: x, startY: y, before: boxes };
      setBoxes(next);
    } else {
      setSelected(-1);
    }
  }

  function pointerMove(event: PointerEvent<HTMLCanvasElement>) {
    if (readOnly) return;
    const gesture = gestureRef.current;
    if (!gesture) return;
    const { x, y } = coords(event);
    const dx = x - gesture.startX;
    const dy = y - gesture.startY;
    const next = [...boxes];
    if (gesture.kind === "draw") {
      const x1 = Math.min(gesture.startX, x);
      const y1 = Math.min(gesture.startY, y);
      next[gesture.index] = { x: x1, y: y1, width: Math.abs(dx), height: Math.abs(dy), label: activeLabel };
    } else if (gesture.original) {
      const original = gesture.original;
      next[gesture.index] = gesture.kind === "move"
        ? { ...original, x: original.x + dx, y: original.y + dy }
        : resizeBox(original, gesture.handle || "se", dx, dy, dimensions.width, dimensions.height, gesture.symmetric);
      next[gesture.index] = clampBox(next[gesture.index], dimensions.width, dimensions.height);
    }
    setBoxes(next);
    onChange(replaceRectangles(annotation, next));
  }

  function pointerUp() {
    if (readOnly) {
      gestureRef.current = null;
      return;
    }
    const gesture = gestureRef.current;
    if (!gesture) return;
    gestureRef.current = null;
    let next = [...boxes];
    if (gesture.kind === "draw") {
      const box = next[gesture.index];
      if (!box || box.width < MIN_SIZE || box.height < MIN_SIZE) next = next.filter((_item, index) => index !== gesture.index);
    }
    commit(gesture.before);
    emit(next);
    if (selected >= next.length) setSelected(next.length - 1);
  }

  function undo() {
    const previous = undoRef.current.pop();
    if (!previous) return;
    redoRef.current.push(boxes);
    setUndoCount(undoRef.current.length);
    setRedoCount(redoRef.current.length);
    setSelected(-1);
    emit(previous);
  }

  function redo() {
    const next = redoRef.current.pop();
    if (!next) return;
    undoRef.current.push(boxes);
    setUndoCount(undoRef.current.length);
    setRedoCount(redoRef.current.length);
    setSelected(-1);
    emit(next);
  }

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (readOnly) return;
    const target = event.target as HTMLElement;
    if (["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z") {
      event.preventDefault();
      if (event.shiftKey) redo(); else undo();
    } else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "y") {
      event.preventDefault();
      redo();
    } else if (selected >= 0 && ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) {
      event.preventDefault();
      const step = event.shiftKey ? 10 : 1;
      const dx = event.key === "ArrowLeft" ? -step : event.key === "ArrowRight" ? step : 0;
      const dy = event.key === "ArrowUp" ? -step : event.key === "ArrowDown" ? step : 0;
      const next = boxes.map((box, index) => {
        if (index !== selected) return box;
        const changed = event.altKey
          ? { ...box, width: Math.max(MIN_SIZE, box.width + dx), height: Math.max(MIN_SIZE, box.height + dy) }
          : { ...box, x: box.x + dx, y: box.y + dy };
        return clampBox(changed, dimensions.width, dimensions.height);
      });
      commit(boxes);
      emit(next);
    } else if ((event.key === "Delete" || event.key === "Backspace") && selected >= 0) {
      event.preventDefault();
      const next = boxes.filter((_box, index) => index !== selected);
      commit(boxes);
      setSelected(-1);
      emit(next);
    } else if (event.key === "Escape") {
      setSelected(-1);
    }
  }

  function changeLabel(index: number, label: string) {
    const next = boxes.map((box, row) => row === index ? { ...box, label } : box);
    commit(boxes);
    emit(next);
  }

  return (
    <section className="canvas-panel" aria-label="圖片標注編輯器">
      <div className="canvas-toolbar">
        <div className="tool-switch" role="group" aria-label="標注工具">
          <button className={tool === "draw" ? "active" : ""} onClick={() => setTool("draw")} disabled={readOnly} type="button">▱ 框選</button>
          <button className={tool === "select" ? "active" : ""} onClick={() => setTool("select")} disabled={readOnly} type="button">↖ 選取</button>
        </div>
        <label className="label-picker">目前類別
          <select aria-label="目前類別" value={activeLabel} onChange={(event) => setActiveLabel(event.target.value)} disabled={readOnly}>
            {(labels.length ? labels : ["object"]).map((label) => <option key={label}>{label}</option>)}
          </select>
        </label>
        <span className="toolbar-spacer" />
        <button className="quiet-button" onClick={undo} disabled={readOnly || !undoCount} type="button">↶ 復原</button>
        <button className="quiet-button" onClick={redo} disabled={readOnly || !redoCount} type="button">↷ 重做</button>
      </div>
      <div className="canvas-frame" onKeyDown={onKeyDown} tabIndex={0}>
        {dimensions.width > 0 ? (
          <canvas
            ref={canvasRef}
            aria-label="圖片與標注框"
            className={tool === "draw" ? "annotation-canvas draw-mode" : "annotation-canvas"}
            width={dimensions.width}
            height={dimensions.height}
            onPointerDown={pointerDown}
            onPointerMove={pointerMove}
            onPointerUp={pointerUp}
            onPointerCancel={pointerUp}
            tabIndex={0}
          />
        ) : <div className="image-loading">載入圖片中…</div>}
      </div>
      <div className="box-list" aria-label="標注框清單">
        {boxes.length === 0 ? <span className="muted small">拖曳圖片即可新增框選</span> : boxes.map((box, index) => (
          <div className={`box-row ${index === selected ? "selected" : ""}`} key={`${index}-${box.x}-${box.y}`}>
            <button className="box-select" onClick={() => setSelected(index)} type="button">{String(index + 1).padStart(2, "0")}</button>
            <select aria-label={`第 ${index + 1} 個框選類別`} value={box.label} onChange={(event) => changeLabel(index, event.target.value)} disabled={readOnly}>
              {[...new Set([box.label, ...labels])].map((label) => <option key={label}>{label}</option>)}
            </select>
            <span className="box-coordinates">{Math.round(box.width)} × {Math.round(box.height)}</span>
            <button className="icon-button danger-text" aria-label={`刪除第 ${index + 1} 個框選`} disabled={readOnly} onClick={() => {
              commit(boxes);
              setSelected(-1);
              emit(boxes.filter((_item, row) => row !== index));
            }} type="button">×</button>
          </div>
        ))}
      </div>
      <p className="canvas-hint">{readOnly ? "此外部任務已完成，標注內容為唯讀。" : "拖曳框選；拖動框內可移動，選取後用八個把手調整大小；Alt 拖把手可對稱縮放。方向鍵微調位置、Shift 加速，Alt+方向鍵微調大小；Delete 刪除、Ctrl+Z 復原、Ctrl+Y 重做。"}</p>
    </section>
  );
}
