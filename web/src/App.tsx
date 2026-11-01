import { useEffect, useMemo, useState, type FormEvent } from "react";
import { AnnotationCanvas } from "./AnnotationCanvas";
import { api, loadImageUrl } from "./api";
import { filterItems } from "./annotationModel";
import type { Annotation, ExportResult, Item, LabelAnalysis, Manifest, Stage, Workspace } from "./types";

const STAGES: Array<{ id: Stage; label: string; icon: string }> = [
  { id: "data", label: "資料集", icon: "▦" },
  { id: "workspace", label: "標注工作台", icon: "⌖" },
  { id: "review", label: "審核", icon: "✓" },
  { id: "export", label: "匯出", icon: "↗" },
];

const FORMATS = [
  ["coco_json", "COCO JSON", "物件偵測"],
  ["yolo_txt", "YOLO txt", "物件偵測"],
  ["pascal_voc", "Pascal VOC", "物件偵測"],
  ["imagefolder", "ImageFolder", "影像分類"],
  ["csv", "CSV", "通用資料"],
] as const;

function lines(value: string) {
  return [...new Set(value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean))];
}

function reviewKey(manifestId: string) {
  return `annotation-review-confirmed:${manifestId}`;
}

function shortError(error: unknown) {
  return error instanceof Error ? error.message : "本機服務發生錯誤";
}

export default function App() {
  const [manifests, setManifests] = useState<Manifest[]>([]);
  const [manifestId, setManifestId] = useState("");
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [stage, setStage] = useState<Stage>("workspace");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [panel, setPanel] = useState<"source" | "labels">("source");
  const [folderPath, setFolderPath] = useState("");
  const [datasetName, setDatasetName] = useState("");
  const [recursive, setRecursive] = useState(true);
  const [selectedId, setSelectedId] = useState("");
  const [canvasRevision, setCanvasRevision] = useState(0);
  const [detail, setDetail] = useState<{ item: Item; annotation: Annotation } | null>(null);
  const [imageUrl, setImageUrl] = useState("");
  const [dirty, setDirty] = useState(false);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [classificationFilter, setClassificationFilter] = useState("all");
  const [confidenceFilter, setConfidenceFilter] = useState("all");
  const [labelFilter, setLabelFilter] = useState("all");
  const [page, setPage] = useState(0);
  const [labelsText, setLabelsText] = useState("");
  const [classificationLabelsText, setClassificationLabelsText] = useState("");
  const [tool, setTool] = useState("x-anylabeling");
  const [toolExecutable, setToolExecutable] = useState("");
  const [classification, setClassification] = useState("");
  const [reviewComment, setReviewComment] = useState("");
  const [reviewConfirmed, setReviewConfirmed] = useState(false);
  const [labelAnalysis, setLabelAnalysis] = useState<LabelAnalysis | null>(null);
  const [renameFrom, setRenameFrom] = useState("");
  const [renameTo, setRenameTo] = useState("");
  const [mergeFrom, setMergeFrom] = useState<string[]>([]);
  const [mergeTo, setMergeTo] = useState("");
  const [modelPath, setModelPath] = useState("");
  const [confidence, setConfidence] = useState(0.25);
  const [overwritePredictions, setOverwritePredictions] = useState(false);
  const [exportFormats, setExportFormats] = useState<string[]>(["coco_json"]);
  const [exportDir, setExportDir] = useState("");
  const [allowPartial, setAllowPartial] = useState(false);
  const [enableSplit, setEnableSplit] = useState(false);
  const [split, setSplit] = useState({ train: 70, val: 15, test: 15, stratified: true });
  const [exportResult, setExportResult] = useState<ExportResult | null>(null);
  const [exportHistory, setExportHistory] = useState<Array<{ export_id: string; format: string; item_count: number; created_at: string }>>([]);

  useEffect(() => {
    let active = true;
    api.manifests().then(({ manifests: found }) => {
      if (!active) return;
      setManifests(found);
      if (found[0]) setManifestId(found[0].manifest_id);
      setLoading(false);
    }).catch((reason: unknown) => {
      if (!active) return;
      setError(shortError(reason));
      setLoading(false);
    });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!manifestId) {
      setWorkspace(null);
      return;
    }
    let active = true;
    setBusy(true);
    setError("");
    api.workspace(manifestId, {}).then((value) => {
      if (!active) return;
      setWorkspace(value);
      setLabelsText(value.labels.join("\n"));
      setClassificationLabelsText(value.classification_labels.join("\n"));
      setTool(value.annotation_tool);
      setSelectedId((current) => value.items.some((item) => item.item_id === current) ? current : value.items[0]?.item_id ?? "");
      setBusy(false);
    }).catch((reason: unknown) => {
      if (!active) return;
      setError(shortError(reason));
      setWorkspace(null);
      setBusy(false);
    });
    return () => { active = false; };
  }, [manifestId]);

  useEffect(() => {
    if (!manifestId || !selectedId) {
      setDetail(null);
      setImageUrl("");
      return;
    }
    let active = true;
    let objectUrl = "";
    setDetail(null);
    setImageUrl("");
    setDirty(false);
    api.detail(manifestId, selectedId).then(async (value) => {
      if (!active) return;
      setDetail(value);
      setClassification(value.item.classification ?? "");
      setReviewComment("");
      objectUrl = await loadImageUrl(manifestId, selectedId);
      if (active) setImageUrl(objectUrl);
      else URL.revokeObjectURL(objectUrl);
    }).catch((reason: unknown) => {
      if (active) setError(shortError(reason));
    });
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [manifestId, selectedId]);

  useEffect(() => {
    if (!manifestId) return;
    setReviewConfirmed(localStorage.getItem(reviewKey(manifestId)) === "true");
    setExportResult(null);
    api.exports(manifestId).then(({ exports }) => setExportHistory(exports)).catch(() => setExportHistory([]));
  }, [manifestId]);

  useEffect(() => {
    if (stage !== "data" || panel !== "labels" || !manifestId) return;
    api.labels(manifestId).then(setLabelAnalysis).catch((reason: unknown) => setError(shortError(reason)));
  }, [manifestId, panel, stage]);

  const allItems = workspace?.items ?? [];
  const filteredItems = useMemo(
    () => filterItems(allItems, search, statusFilter, classificationFilter, confidenceFilter, labelFilter),
    [allItems, search, statusFilter, classificationFilter, confidenceFilter, labelFilter],
  );
  const pageSize = 20;
  const pageCount = Math.max(1, Math.ceil(filteredItems.length / pageSize));
  const visibleItems = filteredItems.slice(page * pageSize, (page + 1) * pageSize);
  const currentManifest = manifests.find((item) => item.manifest_id === manifestId);
  const currentItem = detail?.item ?? allItems.find((item) => item.item_id === selectedId) ?? null;
  const imageCount = workspace?.total ?? currentManifest?.item_count ?? 0;
  const approvedCount = allItems.filter((item) => item.review_status === "approved").length;
  const rejectedCount = allItems.filter((item) => item.review_status === "rejected").length;
  const pendingReviewCount = Math.max(0, allItems.length - approvedCount - rejectedCount);
  const selectedItemIndex = allItems.findIndex((item) => item.item_id === selectedId);

  async function refreshWorkspace(id = manifestId) {
    if (!id) return;
    setBusy(true);
    try {
      const value = await api.workspace(id, {});
      setWorkspace(value);
      setManifests((previous) => previous.map((item) => item.manifest_id === id ? { ...item, name: value.manifest_name, item_count: value.total } : item));
      if (id === manifestId && selectedId && value.items.some((item) => item.item_id === selectedId)) {
        const refreshed = await api.detail(id, selectedId);
        setDetail(refreshed);
        setClassification(refreshed.item.classification ?? "");
        setDirty(false);
        setCanvasRevision((current) => current + 1);
      }
    } catch (reason) {
      setError(shortError(reason));
    } finally {
      setBusy(false);
    }
  }

  async function saveCurrentAnnotation() {
    if (!manifestId || !selectedId || !detail || !dirty) return;
    setBusy(true);
    try {
      await api.saveAnnotation(manifestId, selectedId, detail.annotation);
      setDirty(false);
      setNotice("標注已保存");
      await refreshWorkspace();
    } catch (reason) {
      setError(shortError(reason));
    } finally {
      setBusy(false);
    }
  }

  async function chooseItem(itemId: string) {
    if (itemId === selectedId) return;
    if (dirty && detail && manifestId) {
      try {
        await api.saveAnnotation(manifestId, selectedId, detail.annotation);
        setNotice("標注已保存");
      } catch (reason) {
        setError(`切換圖片前無法保存：${shortError(reason)}`);
        return;
      }
    }
    setSelectedId(itemId);
    setDirty(false);
  }

  async function createDataset(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const created = await api.createDataset({ folder_path: folderPath, manifest_name: datasetName, recursive });
      const result = await api.manifests();
      setManifests(result.manifests);
      setManifestId(created.manifest_id);
      setStage("workspace");
      setNotice(`資料集已建立，共 ${created.item_count} 張圖片`);
      setFolderPath("");
      setDatasetName("");
    } catch (reason) {
      setError(shortError(reason));
    } finally {
      setBusy(false);
    }
  }

  async function saveSettings() {
    if (!manifestId) return;
    setBusy(true);
    try {
      const value = await api.workspace(manifestId, {
        labels: lines(labelsText),
        classification_labels: lines(classificationLabelsText),
        annotation_tool: tool,
      });
      setWorkspace(value);
      setNotice("工作區設定已保存");
    } catch (reason) {
      setError(shortError(reason));
    } finally {
      setBusy(false);
    }
  }

  async function saveClassification(label: string) {
    if (!manifestId || !selectedId) return;
    setClassification(label);
    try {
      await api.saveClassification(manifestId, selectedId, label);
      setWorkspace((current) => current ? {
        ...current,
        items: current.items.map((item) => item.item_id === selectedId ? { ...item, classification: label, has_classification: Boolean(label) } : item),
      } : current);
    } catch (reason) {
      setError(shortError(reason));
    }
  }

  async function updateReview(status: "approved" | "rejected" | "pending") {
    if (!manifestId || !selectedId) return;
    setBusy(true);
    try {
      await api.review(manifestId, selectedId, status, reviewComment);
      setWorkspace((current) => current ? {
        ...current,
        items: current.items.map((item) => item.item_id === selectedId ? { ...item, review_status: status } : item),
      } : current);
      setNotice(status === "approved" ? "已核准這張圖片" : status === "rejected" ? "已退回這張圖片" : "已重設為待審");
    } catch (reason) {
      setError(shortError(reason));
    } finally {
      setBusy(false);
    }
  }

  async function updateLabels(action: string, body: Record<string, unknown>) {
    if (!manifestId) return;
    setBusy(true);
    try {
      const result = await api.labelAction(manifestId, action, body) as { updated: number; labels: LabelAnalysis };
      setLabelAnalysis(result.labels);
      setNotice(`已更新 ${result.updated} 個標注`);
    } catch (reason) {
      setError(shortError(reason));
    } finally {
      setBusy(false);
    }
  }

  async function runPrelabel(itemIds?: string[]) {
    if (!manifestId) return;
    if (dirty) {
      setError("請先保存目前標注，再執行 AI 預標注。");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const result = await api.prelabel(manifestId, { model_path: modelPath, confidence, overwrite: overwritePredictions, item_ids: itemIds }) as { ok: number; skipped: number; errors: number; detected: number };
      setNotice(`AI 預標注完成：${result.ok} 張、${result.detected} 個框；略過 ${result.skipped} 張，錯誤 ${result.errors} 張`);
      await refreshWorkspace();
    } catch (reason) {
      setError(shortError(reason));
    } finally {
      setBusy(false);
    }
  }

  async function launchTool() {
    if (!manifestId || !selectedId) return;
    if (dirty && detail) {
      try {
        await api.saveAnnotation(manifestId, selectedId, detail.annotation);
        setDirty(false);
      } catch (reason) {
        setError(`啟動外部工具前無法保存：${shortError(reason)}`);
        return;
      }
    }
    setBusy(true);
    try {
      await api.launchTool(manifestId, selectedId, tool, toolExecutable);
      setNotice("已要求本機標注工具開啟目前圖片");
    } catch (reason) {
      setError(shortError(reason));
    } finally {
      setBusy(false);
    }
  }

  function toggleFormat(format: string) {
    setExportFormats((current) => current.includes(format) ? current.filter((item) => item !== format) : [...current, format]);
  }

  async function exportDataset() {
    if (!manifestId) return;
    if (workspace && workspace.pending > 0 && !allowPartial) {
      setError("還有未標注圖片。請完成標注，或明確勾選允許部分匯出。");
      return;
    }
    if (!reviewConfirmed) {
      setError("請先在審核階段確認已完成必要檢查。");
      return;
    }
    if (!exportDir.trim() || exportFormats.length === 0) {
      setError("請選擇至少一種匯出格式並填寫輸出資料夾。");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const result = await api.export(manifestId, {
        export_formats: exportFormats,
        export_dir: exportDir,
        enable_split: enableSplit,
        split_train: split.train,
        split_val: split.val,
        split_test: split.test,
        stratified: split.stratified,
      });
      setExportResult(result);
      if (result.mode === "done") {
        setNotice("匯出完成");
        const history = await api.exports(manifestId);
        setExportHistory(history.exports);
      }
    } catch (reason) {
      setError(shortError(reason));
    } finally {
      setBusy(false);
    }
  }

  function changeStage(next: Stage) {
    if (dirty && next !== stage) void saveCurrentAnnotation();
    setStage(next);
    setError("");
    setPage(0);
    setStatusFilter("all");
    setClassificationFilter("all");
    setConfidenceFilter("all");
    setLabelFilter("all");
  }

  function markReviewConfirmed(checked: boolean) {
    setReviewConfirmed(checked);
    if (manifestId) localStorage.setItem(reviewKey(manifestId), String(checked));
  }

  function changeStatusFilter(value: string) {
    setStatusFilter(value);
    setPage(0);
  }

  if (loading) return <main className="boot-screen"><div className="brand-mark">A</div><p>正在連線到本機工作區…</p></main>;

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand-lockup"><div className="brand-mark">A</div><div><strong>ANnoTation</strong><span>LOCAL VISION WORKSPACE</span></div></div>
        <div className="topbar-center">
          {currentManifest ? <><span className="status-dot" /><span className="top-dataset">{currentManifest.name}</span><span className="top-count">{imageCount.toLocaleString()} 張圖片</span></> : <span className="muted">尚未選擇資料集</span>}
        </div>
        <div className="topbar-right"><span className="local-badge"><i />本機資料</span><button className="avatar-button" title="本機工作階段">{(navigator.language.startsWith("zh") ? "本" : "L")}</button></div>
      </header>

      <nav className="stage-nav" aria-label="工作流程">
        {STAGES.map((item, index) => <button key={item.id} type="button" className={`stage-tab ${stage === item.id ? "active" : ""} ${STAGES.findIndex((row) => row.id === stage) > index ? "complete" : ""}`} onClick={() => changeStage(item.id)}>
          <span className="stage-icon">{item.icon}</span><span>{item.label}</span>
        </button>)}
        <div className="stage-progress"><span style={{ width: `${((STAGES.findIndex((item) => item.id === stage) + 1) / STAGES.length) * 100}%` }} /></div>
      </nav>

      <main className="main-content">
        {(error || notice) && <div className={`notice ${error ? "error" : "success"}`} role="status"><span>{error || notice}</span><button onClick={() => { setError(""); setNotice(""); }} aria-label="關閉訊息" type="button">×</button></div>}
        {busy && <div className="busy-line" aria-label="處理中"><span /></div>}

        {stage === "data" && <section className="page-section">
          <div className="page-heading"><div><p className="eyebrow">DATASET MANAGEMENT</p><h1>資料集與標籤</h1><p className="page-description">建立本機影像資料集，設定標注類別並檢視既有資料。</p></div><div className="heading-illustration">▦</div></div>
          <div className="subnav"><button className={panel === "source" ? "selected" : ""} onClick={() => setPanel("source")} type="button">資料來源</button><button className={panel === "labels" ? "selected" : ""} onClick={() => setPanel("labels")} type="button">標籤管理</button></div>
          {panel === "source" ? <div className="data-layout">
            <section className="card source-card"><div className="card-title"><span className="card-icon mint">＋</span><div><h2>新增本機資料集</h2><p>選擇含有圖片的資料夾作為工作來源</p></div></div>
              <form onSubmit={createDataset} className="form-stack">
                <label>資料夾路徑<input value={folderPath} onChange={(event) => setFolderPath(event.target.value)} placeholder="例如：D:\\datasets\\inspection" required /></label>
                <p className="field-help">圖片路徑只在本機服務中使用，不會傳送到外部伺服器。</p>
                <label>資料集名稱<input value={datasetName} onChange={(event) => setDatasetName(event.target.value)} placeholder="留空時使用資料夾名稱" /></label>
                <label className="checkbox-row"><input type="checkbox" checked={recursive} onChange={(event) => setRecursive(event.target.checked)} /><span>包含子資料夾</span></label>
                <div className="supported-types"><span>支援格式</span><div>{["JPG", "PNG", "BMP", "WEBP", "TIFF"].map((item) => <b key={item}>{item}</b>)}</div></div>
                <button className="primary-button" type="submit" disabled={busy || !folderPath.trim()}>建立資料集並開始標注 <span>→</span></button>
              </form>
              <div className="source-note"><span>i</span><p>目前 React 工作流程支援本機資料夾。原流程的遠端服務與 iWISC 任務來源仍列在相容性清單中。</p></div>
            </section>
            <section className="card manifest-card"><div className="section-title"><div><h2>最近使用</h2><p>選擇資料集以繼續工作</p></div><span className="count-pill">{manifests.length} 個資料集</span></div>
              {manifests.length === 0 ? <div className="empty-card"><span>▦</span><h3>還沒有資料集</h3><p>輸入本機資料夾路徑後建立第一個工作區。</p></div> : <div className="manifest-list">{manifests.map((item) => <button key={item.manifest_id} className={`manifest-row ${manifestId === item.manifest_id ? "current" : ""}`} onClick={() => { setManifestId(item.manifest_id); setStage("workspace"); }} type="button">
                <span className="dataset-file-icon">▧</span><span className="manifest-name"><strong>{item.name}</strong><small>{item.source_type === "folder" ? "本機資料夾" : item.source_type}</small></span><span className="manifest-count">{item.item_count.toLocaleString()} <small>張</small></span><span className="row-arrow">›</span>
              </button>)}</div>}
            </section>
          </div> : <div className="labels-layout">
            <section className="card label-config"><div className="section-title"><div><h2>類別設定</h2><p>供 Canvas 標注工具與快速分類使用</p></div></div>
              <label>框選標籤<textarea rows={7} value={labelsText} onChange={(event) => setLabelsText(event.target.value)} placeholder={'scratch\ndent\nstain'} /></label><span className="field-help">每行一個類別名稱。既有多邊形與其他形狀會在編輯矩形框時保留。</span>
              <label>整張圖片分類<textarea rows={5} value={classificationLabelsText} onChange={(event) => setClassificationLabelsText(event.target.value)} placeholder={'OK\nNG\n待確認'} /></label>
              <label>外部標注工具<select value={tool} onChange={(event) => setTool(event.target.value)}><option value="x-anylabeling">X-AnyLabeling</option><option value="labelme">LabelMe</option><option value="isat">ISAT-SAM</option></select></label>
              <label>工具執行檔路徑（選填）<input value={toolExecutable} onChange={(event) => setToolExecutable(event.target.value)} placeholder="留空時使用系統已安裝的工具" /></label>
              <button className="primary-button" disabled={!manifestId || busy} onClick={() => void saveSettings()} type="button">保存工作區設定 <span>→</span></button>
            </section>
            <section className="card label-stats"><div className="section-title"><div><h2>標籤使用狀況</h2><p>{labelAnalysis?.total_items ?? imageCount} 張圖片的掃描結果</p></div><button className="quiet-button" onClick={() => api.labels(manifestId).then(setLabelAnalysis).catch((reason: unknown) => setError(shortError(reason)))} type="button">↻ 重新掃描</button></div>
              {labelAnalysis?.label_map && Object.keys(labelAnalysis.label_map).length ? <div className="label-table"><div className="label-table-head"><span>標籤</span><span>框數 / 圖片</span><span>操作</span></div>{Object.entries(labelAnalysis.label_map).map(([label, imageCount]) => <div className="label-table-row" key={label}><span className="label-tag">{label}</span><span>{labelAnalysis.label_counts[label] ?? 0}<small>{imageCount} 張圖片</small></span><div><button className="text-action" onClick={() => { setRenameFrom(label); setRenameTo(label); }} type="button">改名</button><button className="text-action danger-text" onClick={() => { if (window.confirm(`確定刪除「${label}」？這會影響 ${imageCount} 張圖片。`)) void updateLabels("delete", { label }); }} type="button">刪除</button></div></div>)}</div> : <div className="empty-card compact"><span>◇</span><h3>尚無框選標籤</h3><p>開始標注後，標籤統計會顯示在這裡。</p></div>}
              {labelAnalysis?.near_dupes?.length ? <div className="duplicate-list"><h3>可能相近的標籤</h3>{labelAnalysis.near_dupes.map((pair) => <p key={pair.join(":")}><span>{pair[0]}</span><i>↔</i><span>{pair[1]}</span></p>)}</div> : null}
              {renameFrom && <div className="inline-edit"><h3>重新命名標籤</h3><p className="field-help">將更新 {labelAnalysis?.label_map[renameFrom] ?? 0} 張圖片中的標籤。</p><div className="inline-fields"><input aria-label="原標籤" value={renameFrom} onChange={(event) => setRenameFrom(event.target.value)} /><span>→</span><input aria-label="新標籤" value={renameTo} onChange={(event) => setRenameTo(event.target.value)} /></div><div className="inline-actions"><button className="quiet-button" onClick={() => setRenameFrom("")} type="button">取消</button><button className="primary-button small-button" disabled={!renameTo.trim() || renameTo === renameFrom} onClick={() => { void updateLabels("rename", { old: renameFrom, new: renameTo }); setRenameFrom(""); }} type="button">確認改名</button></div></div>}
              {labelAnalysis?.label_map && Object.keys(labelAnalysis.label_map).length > 1 && <div className="merge-box"><h3>合併標籤</h3><div className="inline-fields"><select aria-label="選擇要合併的標籤" multiple value={mergeFrom} onChange={(event) => setMergeFrom(Array.from(event.target.selectedOptions, (option) => option.value))}>{Object.keys(labelAnalysis.label_map).map((label) => <option key={label}>{label}</option>)}</select><span>→</span><select aria-label="合併後保留的標籤" value={mergeTo} onChange={(event) => setMergeTo(event.target.value)}><option value="">選擇目標</option>{Object.keys(labelAnalysis.label_map).map((label) => <option key={label}>{label}</option>)}</select></div><button className="quiet-button" disabled={!mergeFrom.length || !mergeTo || mergeFrom.includes(mergeTo)} onClick={() => { void updateLabels("merge", { sources: mergeFrom, target: mergeTo }); setMergeFrom([]); }} type="button">合併選取標籤</button></div>}
            </section>
          </div>}
        </section>}

        {(stage === "workspace" || stage === "review") && <section className="workbench-page">
          <div className="workbench-heading"><div><p className="eyebrow">{stage === "workspace" ? "ANNOTATION WORKSPACE" : "QUALITY REVIEW"}</p><h1>{stage === "workspace" ? "標注工作台" : "審核佇列"}</h1><p className="page-description">{currentManifest?.name ?? "選擇一個資料集以開始"}</p></div><div className="workbench-actions">
            <select aria-label="目前資料集" value={manifestId} onChange={(event) => setManifestId(event.target.value)}>{manifests.map((item) => <option key={item.manifest_id} value={item.manifest_id}>{item.name} · {item.item_count} 張</option>)}</select>
            <button className="quiet-button" onClick={() => void refreshWorkspace()} disabled={!manifestId || busy || dirty} type="button">↻ 重新掃描</button>
          </div></div>
          {!manifestId ? <div className="empty-state"><span>▦</span><h2>先建立或選擇資料集</h2><p>到「資料集」加入本機影像資料夾。</p><button className="primary-button" onClick={() => setStage("data")} type="button">前往資料集 <span>→</span></button></div> : <>
            <div className="stats-strip"><div><span className="metric-icon blue">▧</span><div><small>總圖片</small><strong>{imageCount.toLocaleString()}</strong></div></div><div><span className="metric-icon mint">✓</span><div><small>已標注</small><strong>{(imageCount - (workspace?.pending ?? imageCount)).toLocaleString()}</strong></div></div><div><span className="metric-icon amber">◷</span><div><small>待標注</small><strong>{(workspace?.pending ?? imageCount).toLocaleString()}</strong></div></div><div><span className="metric-icon violet">◇</span><div><small>{stage === "review" ? "待審核" : "已分類"}</small><strong>{stage === "review" ? pendingReviewCount.toLocaleString() : allItems.filter((item) => item.has_classification).length.toLocaleString()}</strong></div></div><div className="completion-card"><span>完成進度</span><strong>{imageCount ? Math.round(((imageCount - (workspace?.pending ?? imageCount)) / imageCount) * 100) : 0}%</strong><div className="progress-track"><i style={{ width: `${imageCount ? ((imageCount - (workspace?.pending ?? imageCount)) / imageCount) * 100 : 0}%` }} /></div></div></div>

            <div className="workbench-grid">
              <aside className="item-sidebar card">
                <div className="item-sidebar-header"><div><h2>{stage === "review" ? "待審項目" : "影像清單"}</h2><span>{filteredItems.length} 張符合條件</span></div><button className="icon-button" onClick={() => setSearch("")} aria-label="清除搜尋" type="button">⌕</button></div>
                <label className="search-box"><span>⌕</span><input value={search} onChange={(event) => { setSearch(event.target.value); setPage(0); }} placeholder="搜尋影像名稱…" /><kbd>⌘ K</kbd></label>
                <div className="filter-row"><select aria-label="影像篩選" value={statusFilter} onChange={(event) => changeStatusFilter(event.target.value)}>
                  <option value="all">全部狀態</option>
                  {stage === "workspace" ? <><option value="pending">待標注</option><option value="annotated">已標注</option><option value="unclassified">未分類</option><option value="classified">已分類</option></> : <><option value="review-pending">待審查</option><option value="approved">已核准</option><option value="rejected">已退回</option></>}
                </select><button className="filter-clear" disabled={statusFilter === "all" && classificationFilter === "all" && confidenceFilter === "all" && labelFilter === "all" && !search} onClick={() => { setStatusFilter("all"); setClassificationFilter("all"); setConfidenceFilter("all"); setLabelFilter("all"); setSearch(""); setPage(0); }} type="button">清除</button></div>
                {stage === "workspace" ? <div className="secondary-filters"><select aria-label="分類篩選" value={classificationFilter} onChange={(event) => { setClassificationFilter(event.target.value); setPage(0); }}><option value="all">全部分類</option><option value="unclassified">未分類</option>{workspace?.classification_labels.map((label) => <option key={label}>{label}</option>)}</select><select aria-label="AI 信心度" value={confidenceFilter} onChange={(event) => { setConfidenceFilter(event.target.value); setPage(0); }}><option value="all">全部信心度</option><option value="low">低信心度 (&lt; 0.50)</option></select></div> : <div className="secondary-filters"><select aria-label="標籤篩選" value={labelFilter} onChange={(event) => { setLabelFilter(event.target.value); setPage(0); }}><option value="all">全部標籤</option>{workspace?.labels.map((label) => <option key={label}>{label}</option>)}</select></div>}
                <div className="item-list" aria-label="影像清單">{visibleItems.map((item) => <button key={item.item_id} className={`item-row ${item.item_id === selectedId ? "selected" : ""}`} onClick={() => void chooseItem(item.item_id)} type="button">
                  <span className="item-thumb"><span className="thumb-placeholder">▧</span>{item.has_annotation && <i />}</span><span className="item-meta"><strong title={item.name}>{item.name}</strong><small>{item.shape_count ?? 0} 個框選{item.classification ? ` · ${item.classification}` : ""}</small></span><span className={`review-chip ${item.review_status ?? "pending"}`}>{item.review_status === "approved" ? "已核准" : item.review_status === "rejected" ? "已退回" : item.has_annotation ? "已標注" : "待標注"}</span>
                </button>)}{visibleItems.length === 0 && <div className="list-empty"><span>⌕</span><p>沒有符合條件的影像</p><button className="text-action" onClick={() => { setSearch(""); setStatusFilter("all"); }} type="button">清除全部篩選</button></div>}</div>
                <div className="pagination"><button disabled={page === 0} onClick={() => setPage((current) => Math.max(0, current - 1))} aria-label="上一頁" type="button">‹</button><span>{page + 1} / {pageCount}</span><button disabled={page >= pageCount - 1} onClick={() => setPage((current) => Math.min(pageCount - 1, current + 1))} aria-label="下一頁" type="button">›</button></div>
              </aside>

              <section className="editor-column">
                {currentItem && detail ? <>
                  <div className="image-header"><div><div className="crumbs"><span>影像</span><i>/</i><strong>{currentItem.name}</strong></div><div className="image-meta"><span>{currentItem.width ?? "—"} × {currentItem.height ?? "—"} px</span><span>·</span><span>{detail.annotation.shapes.length} 個圖形</span><span>·</span><span className={`save-state ${dirty ? "unsaved" : ""}`}>{dirty ? "尚未儲存" : "已同步"}</span></div></div><div className="image-header-actions"><button className="quiet-button" onClick={() => void launchTool()} disabled={busy} type="button">↗ 外部工具</button><button className="primary-button compact-button" onClick={() => void saveCurrentAnnotation()} disabled={!dirty || busy} type="button">保存標注 <span>↵</span></button></div></div>
                  <AnnotationCanvas key={`${selectedId}-${canvasRevision}`} itemKey={selectedId} imageUrl={imageUrl} annotation={detail.annotation} labels={workspace?.labels ?? []} onChange={(annotation) => { setDetail((current) => current ? { ...current, annotation } : current); setDirty(true); }} />
                  <div className="image-footer"><button className="quiet-button" disabled={selectedItemIndex <= 0} onClick={() => void chooseItem(allItems[selectedItemIndex - 1]?.item_id ?? "")} type="button">← 上一張</button><span>{Math.max(0, selectedItemIndex + 1)} / {allItems.length}</span><button className="quiet-button" disabled={selectedItemIndex >= allItems.length - 1} onClick={() => void chooseItem(allItems[selectedItemIndex + 1]?.item_id ?? "")} type="button">下一張 →</button></div>
                </> : <div className="empty-canvas"><span>▧</span><p>{allItems.length ? "選擇一張影像以開始" : "這個資料集目前沒有可用圖片"}</p></div>}
              </section>

              <aside className="inspector-column">
                {stage === "workspace" ? <>
                  <section className="card inspector-card"><div className="inspector-title"><span className="inspector-icon blue">◉</span><div><h2>影像分類</h2><p>快速設定整張圖片類別</p></div></div>
                    <label className="field-label">分類結果<select value={classification} onChange={(event) => void saveClassification(event.target.value)} disabled={!currentItem}><option value="">尚未分類</option>{(workspace?.classification_labels ?? []).map((label) => <option key={label}>{label}</option>)}{classification && !workspace?.classification_labels.includes(classification) && <option>{classification}</option>}</select></label>
                    <div className="quick-labels">{(workspace?.classification_labels ?? []).slice(0, 6).map((label) => <button className={classification === label ? "active" : ""} key={label} onClick={() => void saveClassification(label)} type="button">{label}</button>)}</div>
                  </section>
                  <section className="card inspector-card"><div className="inspector-title"><span className="inspector-icon violet">✦</span><div><h2>AI 預標注</h2><p>使用本機 YOLO 模型建議框選</p></div></div>
                    <label className="field-label">模型檔案路徑<input value={modelPath} onChange={(event) => setModelPath(event.target.value)} placeholder="選擇本機 .pt 模型" /></label>
                    <label className="field-label range-label">信心度 <strong>{confidence.toFixed(2)}</strong><input type="range" min="0" max="1" step="0.01" value={confidence} onChange={(event) => setConfidence(Number(event.target.value))} /></label>
                    <label className="checkbox-row small-check"><input type="checkbox" checked={overwritePredictions} onChange={(event) => setOverwritePredictions(event.target.checked)} /><span>覆寫現有標注</span></label>
                    <div className="prelabel-actions"><button className="secondary-button" onClick={() => void runPrelabel(selectedId ? [selectedId] : [])} disabled={!modelPath.trim() || !currentItem || busy} type="button">✦ 對此圖執行</button><button className="secondary-button" onClick={() => void runPrelabel()} disabled={!modelPath.trim() || busy} type="button">對資料集執行</button></div><span className="field-help">每批最多 500 張圖片。可調整信心度及覆寫現有標注。</span>
                  </section>
                  <section className="card inspector-card settings-mini"><div className="inspector-title"><span className="inspector-icon mint">⚙</span><div><h2>標注設定</h2><p>{workspace?.labels.length ?? 0} 個框選類別</p></div></div><label className="field-label">外部標注工具<select value={tool} onChange={(event) => setTool(event.target.value)}><option value="x-anylabeling">X-AnyLabeling</option><option value="labelme">LabelMe</option><option value="isat">ISAT-SAM</option></select></label><label className="field-label">工具執行檔（選填）<input value={toolExecutable} onChange={(event) => setToolExecutable(event.target.value)} placeholder="工具未加入 PATH 時指定" /></label><button className="text-action" onClick={() => { setStage("data"); setPanel("labels"); }} type="button">管理標籤與分類 →</button></section>
                </> : <section className="card inspector-card review-card"><div className="inspector-title"><span className="inspector-icon amber">✓</span><div><h2>審核決策</h2><p>檢視框選與分類後記錄結果</p></div></div>
                    <div className="review-stats"><div><small>待審查</small><strong>{pendingReviewCount}</strong></div><div><small>已核准</small><strong>{approvedCount}</strong></div><div><small>已退回</small><strong>{rejectedCount}</strong></div></div>
                    <label className="field-label">審核備註<textarea rows={4} value={reviewComment} onChange={(event) => setReviewComment(event.target.value)} placeholder="輸入需要修正的內容或審核說明…" /></label>
                    <div className="review-actions"><button className="reject-button" onClick={() => void updateReview("rejected")} disabled={!currentItem || busy} type="button">退回修正</button><button className="approve-button" onClick={() => void updateReview("approved")} disabled={!currentItem || busy} type="button">✓ 核准標注</button></div>
                    {currentItem?.review_status !== "pending" && <button className="text-action" onClick={() => void updateReview("pending")} type="button">重設為待審</button>}
                    <div className="review-attestation"><label className="checkbox-row"><input type="checkbox" checked={reviewConfirmed} onChange={(event) => markReviewConfirmed(event.target.checked)} /><span>我已完成必要抽查與異常處理</span></label><p>此確認會保存在本機，並作為匯出前檢查的一部分。</p></div>
                  </section>}
              </aside>
            </div>
          </>}
        </section>}

        {stage === "export" && <section className="page-section export-page">
          <div className="page-heading"><div><p className="eyebrow">EXPORT & DELIVERY</p><h1>匯出資料</h1><p className="page-description">檢查資料完整性並建立訓練格式資料集。</p></div><div className="heading-illustration export-illustration">↗</div></div>
          {!manifestId ? <div className="empty-state"><h2>請先建立資料集</h2><button className="primary-button" onClick={() => setStage("data")} type="button">前往資料集 <span>→</span></button></div> : <div className="export-layout">
            <div className="export-main">
              <section className="card preflight-card"><div className="section-title"><div><h2>匯出前檢查</h2><p>目前資料集：{currentManifest?.name}</p></div><span className={`preflight-status ${workspace?.pending === 0 && reviewConfirmed ? "ready" : "needs-attention"}`}>{workspace?.pending === 0 && reviewConfirmed ? "準備就緒" : "需處理"}</span></div>
                <div className="preflight-grid"><div className="preflight-metric"><span className="metric-icon blue">▧</span><div><small>總圖片</small><strong>{imageCount}</strong></div></div><div className="preflight-metric"><span className="metric-icon mint">✓</span><div><small>有框選</small><strong>{imageCount - (workspace?.pending ?? imageCount)}</strong></div></div><div className="preflight-metric"><span className="metric-icon amber">◷</span><div><small>待標注</small><strong>{workspace?.pending ?? imageCount}</strong></div></div><div className="preflight-metric"><span className="metric-icon violet">✦</span><div><small>審核確認</small><strong>{reviewConfirmed ? "已確認" : "未確認"}</strong></div></div></div>
                {workspace && workspace.pending > 0 && <label className="partial-warning"><span>!</span><span><strong>仍有 {workspace.pending} 張圖片未標注</strong><small>預設阻擋匯出。如需輸出部分資料，請明確勾選確認。</small></span><input type="checkbox" aria-label="允許部分匯出" checked={allowPartial} onChange={(event) => setAllowPartial(event.target.checked)} />允許部分匯出</label>}
                {!reviewConfirmed && <div className="inline-warning"><span>!</span><p>尚未確認審核完成。<button className="text-action" onClick={() => setStage("review")} type="button">前往審核</button></p></div>}
              </section>
              <section className="card format-card"><div className="section-title"><div><h2>輸出格式</h2><p>可同時選擇多種格式</p></div></div><div className="format-grid">{FORMATS.map(([id, title, description]) => <label className={`format-option ${exportFormats.includes(id) ? "selected" : ""}`} key={id}><input type="checkbox" checked={exportFormats.includes(id)} onChange={() => toggleFormat(id)} /><span className="format-check">✓</span><span className="format-copy"><strong>{title}</strong><small>{description}</small></span><span className="format-symbol">{id === "csv" ? "▤" : id === "imagefolder" ? "▧" : "{ }"}</span></label>)}</div>
                <div className="split-settings"><label className="checkbox-row"><input type="checkbox" checked={enableSplit} onChange={(event) => setEnableSplit(event.target.checked)} /><span>啟用 Train / Val / Test 分割</span></label>{enableSplit && <div className="split-fields">{(["train", "val", "test"] as const).map((key) => <label key={key}>{key.toUpperCase()} (%)<input type="number" min="0" max="100" value={split[key]} onChange={(event) => setSplit((current) => ({ ...current, [key]: Number(event.target.value) }))} /></label>)}<label className="checkbox-row"><input type="checkbox" checked={split.stratified} onChange={(event) => setSplit((current) => ({ ...current, stratified: event.target.checked }))} /><span>依分類分層</span></label><small>目前合計：{split.train + split.val + split.test}%（匯出時會正規化）</small></div>}</div>
              </section>
              <section className="card destination-card"><div className="section-title"><div><h2>輸出位置</h2><p>選擇本機資料夾，匯出檔案不會離開此裝置</p></div></div><label className="field-label">資料夾路徑<input value={exportDir} onChange={(event) => setExportDir(event.target.value)} placeholder="例如：D:\\exports\\inspection-v1" /></label><button className="primary-button export-button" onClick={() => void exportDataset()} disabled={busy || !manifestId}>檢查並開始匯出 <span>→</span></button></section>
              {exportResult && <section className={`card export-result ${exportResult.mode === "done" ? "done" : "blocked"}`}><div className="section-title"><div><h2>{exportResult.mode === "done" ? "匯出完成" : "匯出檢查結果"}</h2><p>{exportResult.error ?? `${exportResult.annotated_items ?? 0} / ${exportResult.total_items ?? 0} 張圖片已處理`}</p></div><span>{exportResult.mode === "done" ? "✓" : "!"}</span></div>{exportResult.validation_issues?.length ? <div className="validation-list">{exportResult.validation_issues.slice(0, 30).map((issue, index) => <p className={`validation-issue ${issue.severity}`} key={`${issue.code}-${issue.item_id}-${index}`}><span>{issue.severity === "error" ? "×" : issue.severity === "warning" ? "!" : "i"}</span>{issue.message}</p>)}</div> : null}</section>}
            </div>
            <aside className="card export-aside"><span className="export-aside-icon">◈</span><p className="eyebrow">DATASET SUMMARY</p><h2>{currentManifest?.name}</h2><div className="summary-row"><span>影像總數</span><strong>{imageCount}</strong></div><div className="summary-row"><span>已標注影像</span><strong>{imageCount - (workspace?.pending ?? imageCount)}</strong></div><div className="summary-row"><span>已審核</span><strong>{approvedCount}</strong></div><div className="summary-row"><span>分類影像</span><strong>{allItems.filter((item) => item.has_classification).length}</strong></div><div className="aside-divider" /><h3>最近匯出</h3>{exportHistory.length ? exportHistory.slice(0, 5).map((item) => <div className="history-row" key={item.export_id}><span>▤</span><div><strong>{item.format}</strong><small>{item.item_count} 筆 · {item.created_at}</small></div></div>) : <p className="muted small">尚無匯出記錄</p>}</aside>
          </div>}
        </section>}
      </main>

      <footer className="app-footer"><span>ANnoTation Local Workspace</span><span><i />本機執行 · 資料儲存在此裝置</span><button className="text-action" onClick={() => setStage("data")} type="button">工作區設定</button></footer>
    </div>
  );
}
