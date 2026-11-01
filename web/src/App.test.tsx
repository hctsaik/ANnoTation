import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";

const mocks = vi.hoisted(() => ({
  api: {
    manifests: vi.fn(),
    workspace: vi.fn(),
    detail: vi.fn(),
    exports: vi.fn(),
    remoteDatasets: vi.fn(),
    saveAnnotation: vi.fn(),
    createDataset: vi.fn(),
    review: vi.fn(),
    saveClassification: vi.fn(),
    export: vi.fn(),
  },
  loadImageUrl: vi.fn(),
}));

vi.mock("./api", () => ({ api: mocks.api, loadImageUrl: mocks.loadImageUrl }));

const manifest = { manifest_id: "demo", name: "Inspection set", source_type: "folder", created_at: "2026-01-01", item_count: 2 };
const itemA = { item_id: "img-a", name: "panel-a.png", image_url: "image", width: 40, height: 30, has_annotation: true, has_bbox: true, has_classification: true, classification: "OK", shape_count: 1, labels: ["scratch"], review_status: "pending" as const };
const itemB = { item_id: "img-b", name: "panel-b.png", image_url: "image", width: 40, height: 30, has_annotation: false, has_bbox: false, shape_count: 0, labels: [], review_status: "pending" as const };
const workspace = { manifest_id: "demo", manifest_name: "Inspection set", total: 2, annotated: 1, pending: 1, labels: ["scratch"], classification_labels: ["OK", "NG"], annotation_tool: "x-anylabeling", items: [itemA, itemB] };

describe("ANnoTation workflow shell", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    mocks.api.manifests.mockResolvedValue({ manifests: [manifest] });
    mocks.api.workspace.mockResolvedValue(workspace);
    mocks.api.detail.mockResolvedValue({ item: itemA, annotation: { version: "2.4.0", shapes: [] } });
    mocks.api.exports.mockResolvedValue({ exports: [] });
    mocks.api.remoteDatasets.mockResolvedValue({ datasets: [{ dataset_id: "remote-1", name: "Remote inspection", item_count: 12 }] });
    mocks.api.review.mockResolvedValue({ status: "approved" });
    mocks.api.saveClassification.mockResolvedValue({ classification: "OK" });
    mocks.api.export.mockResolvedValue({ mode: "done", total_items: 2, annotated_items: 1 });
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
