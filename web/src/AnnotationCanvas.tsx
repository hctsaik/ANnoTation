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
  before: Box[];
};

const MIN_SIZE = 3;

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
        ctx.fillRect(box.x + box.width - handle / 2, box.y + box.height - handle / 2, handle, handle);
        ctx.strokeStyle = "#f0643b";
        ctx.strokeRect(box.x + box.width - handle / 2, box.y + box.height - handle / 2, handle, handle);
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
    return {
      x: (event.clientX - rect.left) * (event.currentTarget.width / rect.width),
      y: (event.clientY - rect.top) * (event.currentTarget.height / rect.height),
    };
  }

  function pointerDown(event: PointerEvent<HTMLCanvasElement>) {
    if (readOnly || !dimensions.width || !dimensions.height) return;
    event.currentTarget.focus();
    event.currentTarget.setPointerCapture(event.pointerId);
    const { x, y } = coords(event);
    let hit = -1;
    for (let index = boxes.length - 1; index >= 0; index -= 1) {
      const box = boxes[index];
      if (x >= box.x && x <= box.x + box.width && y >= box.y && y <= box.y + box.height) {
        hit = index;
        break;
      }
    }
    const current = hit >= 0 ? boxes[hit] : undefined;
    const resizeHit = current && Math.abs(x - current.x - current.width) < dimensions.width / 55 && Math.abs(y - current.y - current.height) < dimensions.width / 55;
    if (current) {
      setSelected(hit);
      gestureRef.current = { kind: resizeHit ? "resize" : "move", index: hit, startX: x, startY: y, original: current, before: boxes };
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
        : { ...original, width: Math.max(0, original.width + dx), height: Math.max(0, original.height + dy) };
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
      <p className="canvas-hint">{readOnly ? "此外部任務已完成，標注內容為唯讀。" : "拖曳框選；拖動框內可移動，拖動右下角可調整大小。快捷鍵：Delete 刪除、Ctrl+Z 復原、Ctrl+Y 重做。"}</p>
    </section>
  );
}
