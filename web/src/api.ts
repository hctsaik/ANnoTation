import type { Annotation, ExportResult, IwiscTask, IwiscTenant, Item, LabelAnalysis, LinkedIwiscTask, Manifest, RemoteDataset, Workspace } from "./types";
import { getApiBase } from "./host";

let tokenPromise: Promise<string> | undefined;

async function sessionToken(): Promise<string> {
  tokenPromise ??= getApiBase()
    .then((base) => fetch(`${base}/api/session`))
    .then(async (response) => {
      if (!response.ok) throw new Error("無法連線到本機 ANnoTation 服務");
      const value = (await response.json()) as { token?: string };
      if (!value.token) throw new Error("本機工作階段未準備完成");
      return value.token;
    })
    .catch((error: unknown) => {
      tokenPromise = undefined;
      throw error;
    });
  return tokenPromise;
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = await sessionToken();
  const base = await getApiBase();
  const headers = new Headers(init.headers);
  headers.set("X-Annotation-Token", token);
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const response = await fetch(`${base}${path}`, { ...init, headers });
  if (!response.ok) {
    const value = (await response.json().catch(() => ({}))) as { error?: string };
    throw new Error(value.error || `請求失敗（${response.status}）`);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export async function loadImageUrl(manifestId: string, itemId: string): Promise<string> {
  const token = await sessionToken();
  const base = await getApiBase();
  const response = await fetch(
    `${base}/api/manifests/${encodeURIComponent(manifestId)}/items/${encodeURIComponent(itemId)}/image`,
    { headers: { "X-Annotation-Token": token } },
  );
  if (!response.ok) throw new Error("無法讀取圖片");
  return URL.createObjectURL(await response.blob());
}

export const api = {
  manifests: () => request<{ manifests: Manifest[] }>("/api/manifests"),
  remoteDatasets: (serviceUrl: string) =>
    request<{ datasets: RemoteDataset[] }>(`/api/sources/remote?${new URLSearchParams({ service_url: serviceUrl })}`),
  iwiscTenants: () => request<{ tenants: IwiscTenant[] }>("/api/sources/iwisc/tenants"),
  registerIwiscTenant: (body: Record<string, unknown>) =>
    request<IwiscTenant>("/api/sources/iwisc/tenants", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  iwiscTasks: (tenantId: string) =>
    request<{ tasks: IwiscTask[] }>(`/api/sources/iwisc/tasks?${new URLSearchParams({ tenant_id: tenantId })}`),
  completeIwiscTask: (manifestId: string, annotatedBy: string) =>
    request<{ task: LinkedIwiscTask; delivery: { status: string; error: boolean } }>(
      `/api/manifests/${encodeURIComponent(manifestId)}/iwisc/complete`,
      { method: "POST", body: JSON.stringify({ annotated_by: annotatedBy }) },
    ),
  createDataset: (body: Record<string, unknown>) =>
    request<{ manifest_id: string; name: string; item_count: number; source_type?: string; iwisc_ant_id?: string | null; iwisc_task_id?: string | null }>("/api/manifests", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  workspace: (manifestId: string, body: Record<string, unknown>) =>
    request<Workspace>("/api/workspace", {
      method: "POST",
      body: JSON.stringify({ ...body, manifest_id: manifestId }),
    }),
  items: (manifestId: string, view: "workspace" | "review", filters = {}) =>
    request<{ items: Item[] }>(
      `/api/manifests/${encodeURIComponent(manifestId)}/items?${new URLSearchParams({ view, ...filters })}`,
    ),
  detail: (manifestId: string, itemId: string) =>
    request<{ item: Item; annotation: Annotation }>(
      `/api/manifests/${encodeURIComponent(manifestId)}/items/${encodeURIComponent(itemId)}`,
    ),
  saveAnnotation: (manifestId: string, itemId: string, annotation: Annotation) =>
    request<{ saved: boolean; shape_count: number }>(
      `/api/manifests/${encodeURIComponent(manifestId)}/items/${encodeURIComponent(itemId)}/annotation`,
      { method: "PUT", body: JSON.stringify(annotation) },
    ),
  saveClassification: (manifestId: string, itemId: string, label: string) =>
    request(`/api/manifests/${encodeURIComponent(manifestId)}/items/${encodeURIComponent(itemId)}/classification`, {
      method: "PUT",
      body: JSON.stringify({ label }),
    }),
  review: (manifestId: string, itemId: string, status: string, comment = "") =>
    request(`/api/manifests/${encodeURIComponent(manifestId)}/items/${encodeURIComponent(itemId)}/review`, {
      method: "PUT",
      body: JSON.stringify({ status, comment }),
    }),
  labels: (manifestId: string) =>
    request<LabelAnalysis>(`/api/manifests/${encodeURIComponent(manifestId)}/labels`),
  labelAction: (manifestId: string, action: string, body: Record<string, unknown>) =>
    request(`/api/manifests/${encodeURIComponent(manifestId)}/labels/${action}`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  export: (manifestId: string, body: Record<string, unknown>) =>
    request<ExportResult>(`/api/manifests/${encodeURIComponent(manifestId)}/exports`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  exports: (manifestId: string) =>
    request<{ exports: Array<{ export_id: string; format: string; item_count: number; created_at: string }> }>(
      `/api/manifests/${encodeURIComponent(manifestId)}/exports`,
    ),
  prelabel: (manifestId: string, body: Record<string, unknown>) =>
    request(`/api/manifests/${encodeURIComponent(manifestId)}/prelabel`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  launchTool: (manifestId: string, itemId: string, tool: string, executableOverride = "") =>
    request(`/api/manifests/${encodeURIComponent(manifestId)}/items/${encodeURIComponent(itemId)}/launch`, {
      method: "POST",
      body: JSON.stringify({ tool, executable_override: executableOverride }),
    }),
};
