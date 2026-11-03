import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";

const mocks = vi.hoisted(() => ({
  api: {
    manifests: vi.fn(),
    workspace: vi.fn(),
    detail: vi.fn(),
    exports: vi.fn(),
    remoteDatasets: vi.fn(),
    iwiscTenants: vi.fn(),
    iwiscTasks: vi.fn(),
    registerIwiscTenant: vi.fn(),
    completeIwiscTask: vi.fn(),
    saveAnnotation: vi.fn(),
    createDataset: vi.fn(),
    review: vi.fn(),
    saveClassification: vi.fn(),
    prelabel: vi.fn(),
    uploadLocalModel: vi.fn(),
    launchTool: vi.fn(),
    export: vi.fn(),
    labels: vi.fn(),
    labelAction: vi.fn(),
  },
  loadImageUrl: vi.fn(),
  desktopHost: false,
  nativeAppHost: false,
  pickDirectory: vi.fn(),
  pickExecutable: vi.fn(),
  pickModelFile: vi.fn(),
}));

vi.mock("./api", () => ({ api: mocks.api, loadImageUrl: mocks.loadImageUrl }));
vi.mock("./host", () => ({
  isDesktopHost: () => mocks.desktopHost,
  isNativeAppHost: () => mocks.nativeAppHost,
  supportsNativeFolderPicker: () => mocks.desktopHost || mocks.nativeAppHost,
  pickDirectory: mocks.pickDirectory,
  pickExecutable: mocks.pickExecutable,
  pickModelFile: mocks.pickModelFile,
}));

const manifest = { manifest_id: "demo", name: "Inspection set", source_type: "folder", created_at: "2026-01-01", item_count: 2 };
const itemA = { item_id: "img-a", name: "panel-a.png", image_url: "image", width: 40, height: 30, has_annotation: true, has_bbox: true, has_classification: true, classification: "OK", shape_count: 1, labels: ["scratch"], review_status: "pending" as const };
const itemB = { item_id: "img-b", name: "panel-b.png", image_url: "image", width: 40, height: 30, has_annotation: false, has_bbox: false, shape_count: 0, labels: [], review_status: "pending" as const };
const workspace = { manifest_id: "demo", manifest_name: "Inspection set", total: 2, annotated: 1, pending: 1, labels: ["scratch"], classification_labels: ["OK", "NG"], annotation_tool: "x-anylabeling", items: [itemA, itemB] };

describe("ANnoTation workflow shell", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.desktopHost = false;
    mocks.nativeAppHost = false;
    localStorage.clear();
    mocks.api.manifests.mockResolvedValue({ manifests: [manifest] });
    mocks.api.workspace.mockResolvedValue(workspace);
    mocks.api.detail.mockResolvedValue({ item: itemA, annotation: { version: "2.4.0", shapes: [] } });
    mocks.api.exports.mockResolvedValue({ exports: [] });
    mocks.api.remoteDatasets.mockResolvedValue({ datasets: [{ dataset_id: "remote-1", name: "Remote inspection", item_count: 12 }] });
    mocks.api.iwiscTenants.mockResolvedValue({ tenants: [{ tenant_id: "tenant-1", system_name: "AOI Line 1", server_host_name: "http://aoi.internal", target_format: "coco", has_api_token: true }] });
    mocks.api.iwiscTasks.mockResolvedValue({ tasks: [{ ant_id: "ant-01", ant_active: 0, ant_period: "2026-11", external_context: { lot: "L01" } }] });
    mocks.api.registerIwiscTenant.mockResolvedValue({ tenant_id: "tenant-2", system_name: "AOI Line 2", server_host_name: "http://aoi-2.internal", target_format: "coco", has_api_token: false });
    mocks.api.completeIwiscTask.mockResolvedValue({ task: { task_id: "task-1", ant_id: "ant-01", ant_active: 2, annotated_by: "worker-9", delivery_status: "unsupported" }, delivery: { status: "unsupported", error: false } });
    mocks.api.review.mockResolvedValue({ status: "approved" });
    mocks.api.saveClassification.mockResolvedValue({ classification: "OK" });
    mocks.api.prelabel.mockResolvedValue({ ok: 1, skipped: 0, errors: 0, detected: 2 });
    mocks.api.uploadLocalModel.mockResolvedValue({ model_ref: `local-model:${"a".repeat(64)}.pt`, name: "yolo-local.pt", size_bytes: 4 });
    mocks.api.launchTool.mockResolvedValue({ launched: true, tool: "labelme", item_id: "img-a" });
    mocks.api.export.mockResolvedValue({ mode: "done", total_items: 2, annotated_items: 1 });
    mocks.api.labels.mockResolvedValue({
      manifest_id: "demo",
      label_map: { scratch: 1 },
      label_counts: { scratch: 1 },
      near_dupes: [],
      classification_counts: {},
      total_items: 2,
      annotated_xany: 1,
      export_count: 0,
    });
    mocks.api.labelAction.mockResolvedValue({
      updated: 1,
      labels: {
        manifest_id: "demo",
        label_map: { scratch: 1 },
        label_counts: { scratch: 1 },
        near_dupes: [],
        classification_counts: {},
        total_items: 2,
        annotated_xany: 1,
        export_count: 0,
      },
    });
    mocks.loadImageUrl.mockResolvedValue("blob:local-test");
  });

  it("restores a dataset and exposes the four product stages", async () => {
    render(<App />);
    expect(await screen.findByRole("heading", { name: "標注工作台" })).toBeInTheDocument();
    expect(screen.getAllByText("Inspection set").length).toBeGreaterThan(0);
    const stages = within(screen.getByRole("navigation", { name: "工作流程" }));
    expect(stages.getByRole("button", { name: /資料集/ })).toBeInTheDocument();
    expect(stages.getByRole("button", { name: /審核/ })).toBeInTheDocument();
    expect(stages.getByRole("button", { name: /匯出/ })).toBeInTheDocument();
  });

  it("uses native host pickers to fill local dataset, model, tool and export paths", async () => {
    mocks.desktopHost = true;
    mocks.pickDirectory
      .mockResolvedValueOnce("D:\\datasets\\inspection")
      .mockResolvedValueOnce("D:\\exports\\inspection");
    mocks.pickModelFile.mockResolvedValue("D:\\models\\yolo.pt");
    mocks.pickExecutable.mockResolvedValue("C:\\Tools\\labelme.exe");
    render(<App />);
    await screen.findByRole("heading", { name: "標注工作台" });
    const stages = within(screen.getByRole("navigation", { name: "工作流程" }));

    fireEvent.click(stages.getByRole("button", { name: /資料集/ }));
    fireEvent.click(screen.getByRole("button", { name: "選擇影像資料夾…" }));
    await waitFor(() => expect(screen.getByRole("textbox", { name: "資料夾路徑" })).toHaveValue("D:\\datasets\\inspection"));

    fireEvent.click(stages.getByRole("button", { name: /標注工作台/ }));
    fireEvent.click(screen.getByRole("button", { name: "選擇模型檔案…" }));
    await waitFor(() => expect(screen.getByPlaceholderText("選擇或輸入本機 .pt 模型")).toHaveValue("D:\\models\\yolo.pt"));
    fireEvent.click(screen.getByRole("button", { name: "選擇工具執行檔…" }));
    await waitFor(() => expect(screen.getByPlaceholderText("工具未加入 PATH 時指定")).toHaveValue("C:\\Tools\\labelme.exe"));

    fireEvent.click(stages.getByRole("button", { name: /匯出/ }));
    fireEvent.click(screen.getByRole("button", { name: "選擇匯出資料夾…" }));
    await waitFor(() => expect(screen.getByRole("textbox", { name: "資料夾路徑" })).toHaveValue("D:\\exports\\inspection"));
  });

  it("uses the NativeApp bridge for folders and imports a local model through the same-origin API", async () => {
    mocks.nativeAppHost = true;
    mocks.pickDirectory.mockResolvedValueOnce("D:\\datasets\\inspection").mockResolvedValueOnce("D:\\exports\\inspection");
    render(<App />);
    await screen.findByRole("heading", { name: "標注工作台" });
    const stages = within(screen.getByRole("navigation", { name: "工作流程" }));

    fireEvent.click(stages.getByRole("button", { name: /資料集/ }));
    fireEvent.click(screen.getByRole("button", { name: "選擇影像資料夾…" }));
    await waitFor(() => expect(screen.getByRole("textbox", { name: "資料夾路徑" })).toHaveValue("D:\\datasets\\inspection"));

    fireEvent.click(stages.getByRole("button", { name: /匯出/ }));
    fireEvent.click(screen.getByRole("button", { name: "選擇匯出資料夾…" }));
    await waitFor(() => expect(screen.getByRole("textbox", { name: "資料夾路徑" })).toHaveValue("D:\\exports\\inspection"));

    fireEvent.click(stages.getByRole("button", { name: /標注工作台/ }));
    fireEvent.change(screen.getByLabelText("匯入本機 YOLO 模型"), {
      target: { files: [new File(["model"], "yolo-local.pt", { type: "application/octet-stream" })] },
    });
    await waitFor(() => expect(screen.getByRole("textbox", { name: "模型檔案路徑" })).toHaveValue("yolo-local.pt"));
    fireEvent.click(screen.getByRole("button", { name: "對資料集執行" }));
    await waitFor(() => expect(mocks.api.prelabel).toHaveBeenCalled());
    expect(mocks.api.uploadLocalModel).toHaveBeenCalledWith(expect.objectContaining({ name: "yolo-local.pt" }));
    expect(mocks.api.prelabel.mock.calls[0][1]).toMatchObject({ model_path: `local-model:${"a".repeat(64)}.pt` });
    expect(screen.queryByRole("button", { name: "選擇模型檔案…" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "選擇工具執行檔…" })).not.toBeInTheDocument();
  });

  it("runs dataset pre-labeling in 500-image batches and aggregates the results", async () => {
    const items = [
      itemA,
      ...Array.from({ length: 500 }, (_, index) => ({
        ...itemB,
        item_id: `img-${index}`,
        name: `panel-${index}.png`,
      })),
    ];
    mocks.api.workspace.mockResolvedValue({ ...workspace, total: items.length, items });
    mocks.api.prelabel
      .mockResolvedValueOnce({ ok: 500, skipped: 0, errors: 0, detected: 42 })
      .mockResolvedValueOnce({ ok: 1, skipped: 0, errors: 0, detected: 3 });
    render(<App />);
    await screen.findByRole("heading", { name: "標注工作台" });
    fireEvent.change(screen.getByPlaceholderText("選擇或輸入本機 .pt 模型"), {
      target: { value: "D:\\models\\yolo.pt" },
    });

    fireEvent.click(screen.getByRole("button", { name: "對資料集執行" }));

    await waitFor(() => expect(mocks.api.prelabel).toHaveBeenCalledTimes(2));
    const first = mocks.api.prelabel.mock.calls[0][1] as { item_ids: string[] };
    const second = mocks.api.prelabel.mock.calls[1][1] as { item_ids: string[] };
    expect(first.item_ids).toHaveLength(500);
    expect(second.item_ids).toHaveLength(1);
    expect([...first.item_ids, ...second.item_ids]).toEqual(items.map((item) => item.item_id));
    expect(await screen.findByText(/AI 預標注完成：501 張、45 個框/)).toBeInTheDocument();
  });

  it("reports completed batches and refreshes saved work if a later pre-label batch fails", async () => {
    const items = [
      itemA,
      ...Array.from({ length: 500 }, (_, index) => ({
        ...itemB,
        item_id: `img-${index}`,
        name: `panel-${index}.png`,
      })),
    ];
    mocks.api.workspace.mockResolvedValue({ ...workspace, total: items.length, items });
    mocks.api.prelabel
      .mockResolvedValueOnce({ ok: 500, skipped: 0, errors: 0, detected: 42 })
      .mockRejectedValueOnce(new Error("模型服務中斷"));
    render(<App />);
    await screen.findByRole("heading", { name: "標注工作台" });
    fireEvent.change(screen.getByPlaceholderText("選擇或輸入本機 .pt 模型"), {
      target: { value: "D:\\models\\yolo.pt" },
    });

    fireEvent.click(screen.getByRole("button", { name: "對資料集執行" }));

    expect(await screen.findByText(/前 1 批已完成（500 張、42 個框）/)).toBeInTheDocument();
    expect(screen.getByText(/模型服務中斷/)).toBeInTheDocument();
    expect(mocks.api.workspace).toHaveBeenCalledTimes(2);
  });

  it("launches the selected external annotation tool for the current image", async () => {
    render(<App />);
    await screen.findByRole("heading", { name: "標注工作台" });
    await screen.findByRole("button", { name: /panel-a\.png/ });
    fireEvent.change(screen.getByLabelText("外部標注工具"), { target: { value: "labelme" } });
    fireEvent.change(screen.getByLabelText("工具執行檔（選填）"), {
      target: { value: "D:\\Tools\\labelme.exe" },
    });

    fireEvent.click(await screen.findByRole("button", { name: /外部工具/ }));

    await waitFor(() => expect(mocks.api.launchTool).toHaveBeenCalledWith(
      "demo", "img-a", "labelme", "D:\\Tools\\labelme.exe",
    ));
    expect(await screen.findByText("已要求本機標注工具開啟目前圖片")).toBeInTheDocument();
  });

  it("loads and creates a dataset from a remote Service", async () => {
    mocks.api.createDataset.mockResolvedValue({ manifest_id: "remote-manifest", name: "Remote inspection", item_count: 12 });
    render(<App />);
    await screen.findByRole("heading", { name: "標注工作台" });
    const stages = within(screen.getByRole("navigation", { name: "工作流程" }));
    fireEvent.click(stages.getByRole("button", { name: /資料集/ }));
    fireEvent.change(screen.getByLabelText("來源類型"), { target: { value: "remote" } });
    fireEvent.change(screen.getByLabelText("Service Base URL"), { target: { value: "http://internal.example:8080" } });
    fireEvent.click(screen.getByRole("button", { name: "載入資料集清單" }));
    await screen.findByRole("option", { name: "Remote inspection（12 張）" });
    fireEvent.click(screen.getByRole("button", { name: /下載資料集並開始標注/ }));
    await waitFor(() => expect(mocks.api.createDataset).toHaveBeenCalledWith({
      mode: "remote",
      service_url: "http://internal.example:8080",
      dataset_id: "remote-1",
      dataset_name: "Remote inspection",
      overwrite: false,
    }));
  });

  it("loads iWISC tasks and claims the selected task into a local dataset", async () => {
    mocks.api.createDataset.mockResolvedValue({ manifest_id: "iwisc-manifest", name: "iWISC-ant-01", item_count: 3, iwisc_ant_id: "ant-01" });
    render(<App />);
    await screen.findByRole("heading", { name: "標注工作台" });
    fireEvent.click(within(screen.getByRole("navigation", { name: "工作流程" })).getByRole("button", { name: /資料集/ }));
    fireEvent.change(screen.getByLabelText("來源類型"), { target: { value: "iwisc" } });
    await screen.findByRole("option", { name: /AOI Line 1/ });
    fireEvent.change(screen.getByLabelText("使用者 ID"), { target: { value: "worker-7" } });
    fireEvent.click(screen.getByRole("button", { name: "查看任務清單" }));
    expect(await screen.findByTestId("iwisc-task-ant-01")).toBeInTheDocument();
    expect(mocks.api.iwiscTasks).toHaveBeenCalledWith("tenant-1");
    fireEvent.click(screen.getByTestId("iwisc-task-ant-01"));
    fireEvent.click(screen.getByRole("button", { name: /認領任務並載入影像/ }));
    await waitFor(() => expect(mocks.api.createDataset).toHaveBeenCalledWith({
      mode: "iwisc",
      tenant_id: "tenant-1",
      ant_id: "ant-01",
      user_id: "worker-7",
    }));
    expect(await screen.findByText(/任務 ant-01 已載入/)).toBeInTheDocument();
  });

  it("completes a linked iWISC task and locks its editor when external delivery is unsupported", async () => {
    const openWorkspace = { ...workspace, iwisc_task: { task_id: "task-1", ant_id: "ant-01", ant_active: 1, annotated_by: "worker-7", delivery_status: "" } };
    const completedWorkspace = { ...openWorkspace, iwisc_task: { ...openWorkspace.iwisc_task, ant_active: 2, annotated_by: "worker-9", delivery_status: "unsupported" } };
    mocks.api.workspace.mockReset().mockResolvedValueOnce(openWorkspace).mockResolvedValue(completedWorkspace);
    render(<App />);
    const operator = await screen.findByLabelText("完成任務作業員工號");
    fireEvent.change(operator, { target: { value: "worker-9" } });
    fireEvent.click(screen.getByRole("button", { name: /保存並完成任務/ }));

    await waitFor(() => expect(mocks.api.completeIwiscTask).toHaveBeenCalledWith("demo", "worker-9"));
    expect(within(screen.getByRole("region", { name: "iWISC 任務狀態" })).getByText(/外部結果回傳尚未支援/)).toBeInTheDocument();
    expect(screen.queryByLabelText("完成任務作業員工號")).not.toBeInTheDocument();
    expect(screen.getByText("此外部任務已完成，標注內容為唯讀。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /保存標注/ })).toBeDisabled();
  });

  it("registers an iWISC connection without echoing the API key into the page", async () => {
    render(<App />);
    await screen.findByRole("heading", { name: "標注工作台" });
    fireEvent.click(within(screen.getByRole("navigation", { name: "工作流程" })).getByRole("button", { name: /資料集/ }));
    fireEvent.change(screen.getByLabelText("來源類型"), { target: { value: "iwisc" } });
    fireEvent.click(await screen.findByText("新增外部系統連線"));
    fireEvent.change(screen.getByLabelText("連線別名"), { target: { value: "AOI Line 2" } });
    fireEvent.change(screen.getByLabelText("iWISC Service 位址"), { target: { value: "https://aoi-2.internal" } });
    fireEvent.change(screen.getByLabelText("iWISC API 金鑰"), { target: { value: "secret-value" } });
    fireEvent.change(screen.getByLabelText("iWISC 結果回傳路徑"), { target: { value: "/tasks/{ant_id}/results" } });
    fireEvent.click(screen.getByRole("button", { name: "新增連線" }));
    await waitFor(() => expect(mocks.api.registerIwiscTenant).toHaveBeenCalledWith({
      system_name: "AOI Line 2",
      server_host_name: "https://aoi-2.internal",
      target_format: "coco",
      api_token: "secret-value",
      result_path: "/tasks/{ant_id}/results",
      result_method: "POST",
    }));
    expect(document.body.textContent).not.toContain("secret-value");
  });

  it("previews label-merge impact and requires confirmation before changing labels", async () => {
    const analysis = {
      manifest_id: "demo",
      label_map: { scratch: 2, dent: 1, stain: 1 },
      label_counts: { scratch: 3, dent: 2, stain: 1 },
      near_dupes: [],
      classification_counts: { scratch: 1, stain: 2 },
      total_items: 2,
      annotated_xany: 2,
      export_count: 0,
    };
    mocks.api.labels.mockResolvedValue(analysis);
    mocks.api.labelAction.mockResolvedValue({
      updated: 3,
      labels: {
        ...analysis,
        label_map: { stain: 3 },
        label_counts: { stain: 6 },
        classification_counts: { stain: 3 },
      },
    });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const user = userEvent.setup();

    render(<App />);
    const workflow = within(await screen.findByRole("navigation", { name: "工作流程" }));
    fireEvent.click(workflow.getByRole("button", { name: /資料集/ }));
    fireEvent.click(screen.getByRole("button", { name: "標籤管理" }));
    await screen.findByText("標籤使用狀況");
    await user.selectOptions(screen.getByLabelText("選擇要合併的標籤"), ["scratch", "dent"]);
    await user.selectOptions(screen.getByLabelText("合併後保留的標籤"), "stain");

    const preview = screen.getByTestId("merge-impact-preview");
    expect(preview).toHaveTextContent("修改 3 次標注檔案");
    expect(preview).toHaveTextContent("5 個框與 1 個圖片分類值");
    expect(preview).toHaveTextContent("目標「stain」目前有 1 張圖片、1 個框與 2 個圖片分類值");

    const mergeButton = screen.getByRole("button", { name: "合併選取標籤" });
    fireEvent.click(mergeButton);
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining("3 次標注檔案"));
    expect(mocks.api.labelAction).not.toHaveBeenCalled();

    confirm.mockReturnValue(true);
    fireEvent.click(mergeButton);
    await waitFor(() => expect(mocks.api.labelAction).toHaveBeenCalledWith("demo", "merge", {
      sources: ["scratch", "dent"],
      target: "stain",
    }));
    await waitFor(() => expect(screen.queryByTestId("merge-impact-preview")).not.toBeInTheDocument());
    confirm.mockRestore();
  });

  it("supports reversible pending-work filters", async () => {
    render(<App />);
    await screen.findByRole("button", { name: /panel-a\.png/ });
    fireEvent.change(screen.getByRole("combobox", { name: "影像篩選" }), { target: { value: "pending" } });
    expect(screen.queryByRole("button", { name: /panel-a\.png/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /panel-b\.png/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "清除" }));
    expect(screen.getByRole("button", { name: /panel-a\.png/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /panel-b\.png/ })).toBeInTheDocument();
  });

  it("paginates at twenty items and combines filters after returning to the first page", async () => {
    const items = Array.from({ length: 21 }, (_, index) => {
      const even = index % 2 === 0;
      return {
        item_id: `page-${index}`,
        name: `panel-${String(index).padStart(2, "0")}.png`,
        image_url: "image",
        width: 40,
        height: 30,
        has_annotation: even,
        has_bbox: even,
        has_classification: even,
        classification: even ? "NG" : "OK",
        shape_count: even ? 1 : 0,
        labels: even ? ["scratch"] : [],
        max_conf: index === 20 ? 0.25 : 0.9,
        review_status: "pending" as const,
      };
    });
    mocks.api.workspace.mockResolvedValue({ ...workspace, total: items.length, items });

    render(<App />);
    expect(await screen.findByRole("button", { name: /panel-00\.png/ })).toBeInTheDocument();
    expect(screen.getByText("21 張符合條件")).toBeInTheDocument();
    expect(screen.getByText("1 / 2")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /panel-19\.png/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /panel-20\.png/ })).not.toBeInTheDocument();

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "下一頁" }));
    expect(screen.getByText("2 / 2")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /panel-20\.png/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "下一頁" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "上一頁" })).toBeEnabled();

    fireEvent.change(screen.getByPlaceholderText("搜尋影像名稱…"), { target: { value: "panel-20" } });
    fireEvent.change(screen.getByRole("combobox", { name: "影像篩選" }), { target: { value: "annotated" } });
    fireEvent.change(screen.getByRole("combobox", { name: "分類篩選" }), { target: { value: "NG" } });
    fireEvent.change(screen.getByRole("combobox", { name: "AI 信心度" }), { target: { value: "low" } });
    expect(screen.getByText("1 張符合條件")).toBeInTheDocument();
    expect(screen.getByText("1 / 1")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /panel-20\.png/ })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "清除" }));
    expect(screen.getByText("21 張符合條件")).toBeInTheDocument();
    expect(screen.getByText("1 / 2")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /panel-00\.png/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /panel-20\.png/ })).not.toBeInTheDocument();
  }, 10_000);

  it("filters the review queue by review state and annotation label", async () => {
    const items = [
      { ...itemA, review_status: "approved" as const, labels: ["scratch"] },
      { ...itemB, review_status: "rejected" as const, labels: ["dent"] },
      { ...itemB, item_id: "img-c", name: "panel-c.png", review_status: "approved" as const, labels: ["dent"] },
    ];
    mocks.api.workspace.mockResolvedValue({ ...workspace, total: items.length, items, labels: ["scratch", "dent"] });

    render(<App />);
    await screen.findByRole("button", { name: /panel-a\.png/ });
    const workflow = within(screen.getByRole("navigation", { name: "工作流程" }));
    await userEvent.setup().click(workflow.getByRole("button", { name: /審核$/ }));
    expect(await screen.findByRole("heading", { name: "審核決策" })).toBeInTheDocument();

    const user = userEvent.setup();
    await user.selectOptions(screen.getByRole("combobox", { name: "影像篩選" }), "approved");
    expect(screen.getByRole("button", { name: /panel-a\.png/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /panel-c\.png/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /panel-b\.png/ })).not.toBeInTheDocument();

    await user.selectOptions(screen.getByRole("combobox", { name: "標籤篩選" }), "dent");
    expect(screen.getByText("1 張符合條件")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /panel-c\.png/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /panel-a\.png/ })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "清除" }));
    expect(screen.getByText("3 張符合條件")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /panel-a\.png/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /panel-b\.png/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /panel-c\.png/ })).toBeInTheDocument();
  });

  it("filters by classification and saves image-level classification changes", async () => {
    render(<App />);
    await screen.findByRole("button", { name: /panel-a\.png/ });
    fireEvent.change(screen.getByRole("combobox", { name: "分類篩選" }), { target: { value: "OK" } });
    expect(screen.getByRole("button", { name: /panel-a\.png/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /panel-b\.png/ })).not.toBeInTheDocument();
    fireEvent.change(await screen.findByRole("combobox", { name: "分類結果" }), { target: { value: "NG" } });
    await waitFor(() => expect(mocks.api.saveClassification).toHaveBeenCalledWith("demo", "img-a", "NG"));
  });

  it("records review decisions for the selected image", async () => {
    render(<App />);
    await screen.findByRole("button", { name: /panel-a\.png/ });
    fireEvent.click(screen.getByRole("button", { name: /審核$/ }));
    expect(await screen.findByRole("heading", { name: "審核決策" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /核准標注/ }));
    await waitFor(() => expect(mocks.api.review).toHaveBeenCalledWith("demo", "img-a", "approved", ""));
  });

  it("waits for the selected image details before accepting a review note", async () => {
    let resolveItemBDetail!: (value: { item: typeof itemB; annotation: { version: string; shapes: never[] } }) => void;
    const itemBDetail = new Promise<{ item: typeof itemB; annotation: { version: string; shapes: never[] } }>((resolve) => {
      resolveItemBDetail = resolve;
    });
    mocks.api.detail.mockImplementation((_manifestId: string, itemId: string) => itemId === "img-b"
      ? itemBDetail
      : Promise.resolve({ item: itemA, annotation: { version: "2.4.0", shapes: [] } }));

    render(<App />);
    const user = userEvent.setup();
    await screen.findByRole("button", { name: /panel-a\.png/ });
    const workflow = within(screen.getByRole("navigation", { name: "工作流程" }));
    await user.click(workflow.getByRole("button", { name: /審核$/ }));
    await user.click(await screen.findByRole("button", { name: /panel-b\.png/ }));
    await waitFor(() => expect(mocks.api.detail).toHaveBeenCalledWith("demo", "img-b"));

    const note = screen.getByLabelText("審核備註");
    expect(note).toBeDisabled();
    expect(screen.getByRole("button", { name: /退回修正/ })).toBeDisabled();
    await act(async () => {
      resolveItemBDetail({ item: itemB, annotation: { version: "2.4.0", shapes: [] } });
    });

    await waitFor(() => expect(note).toBeEnabled());
    await user.type(note, "需補標後重送");
    await user.click(screen.getByRole("button", { name: /退回修正/ }));
    await waitFor(() => expect(mocks.api.review).toHaveBeenCalledWith(
      "demo", "img-b", "rejected", "需補標後重送",
    ));
  });

  it("waits for the selected image details before saving its classification", async () => {
    let resolveItemBDetail!: (value: { item: typeof itemB; annotation: { version: string; shapes: never[] } }) => void;
    const itemBDetail = new Promise<{ item: typeof itemB; annotation: { version: string; shapes: never[] } }>((resolve) => {
      resolveItemBDetail = resolve;
    });
    mocks.api.detail.mockImplementation((_manifestId: string, itemId: string) => itemId === "img-b"
      ? itemBDetail
      : Promise.resolve({ item: itemA, annotation: { version: "2.4.0", shapes: [] } }));

    render(<App />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /panel-b\.png/ }));
    await waitFor(() => expect(mocks.api.detail).toHaveBeenCalledWith("demo", "img-b"));

    const classification = screen.getByRole("combobox", { name: "分類結果" });
    expect(classification).toBeDisabled();
    expect(screen.getByRole("button", { name: "NG" })).toBeDisabled();

    await act(async () => {
      resolveItemBDetail({ item: itemB, annotation: { version: "2.4.0", shapes: [] } });
    });

    await waitFor(() => expect(classification).toBeEnabled());
    await user.selectOptions(classification, "NG");
    await waitFor(() => expect(mocks.api.saveClassification).toHaveBeenCalledWith("demo", "img-b", "NG"));
    expect(classification).toHaveValue("NG");
  });

  it("gates partial export on both explicit opt-in and review confirmation", async () => {
    render(<App />);
    await screen.findByRole("heading", { name: "標注工作台" });
    const stages = within(screen.getByRole("navigation", { name: "工作流程" }));
    fireEvent.click(stages.getByRole("button", { name: /匯出/ }));
    await screen.findByRole("heading", { name: "匯出資料" });
    fireEvent.change(screen.getByLabelText("資料夾路徑"), { target: { value: "C:\\exports" } });
    fireEvent.click(screen.getByLabelText("允許部分匯出"));
    fireEvent.click(screen.getByRole("button", { name: /檢查並開始匯出/ }));
    expect(mocks.api.export).not.toHaveBeenCalled();
    expect(screen.getByText(/尚未確認審核完成/)).toBeInTheDocument();

    fireEvent.click(stages.getByRole("button", { name: /審核/ }));
    fireEvent.click(await screen.findByLabelText("我已完成必要抽查與異常處理"));
    fireEvent.click(stages.getByRole("button", { name: /匯出/ }));
    fireEvent.click(await screen.findByRole("button", { name: /檢查並開始匯出/ }));
    await waitFor(() => expect(mocks.api.export).toHaveBeenCalledWith("demo", expect.objectContaining({ export_dir: "C:\\exports" })));
  });
});
