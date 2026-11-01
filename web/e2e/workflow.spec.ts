import { expect, test, type Page } from "@playwright/test";

const initialManifest = { manifest_id: "demo", name: "Inspection set", source_type: "folder", created_at: "2026-01-01", item_count: 2 };
const items = [
  { item_id: "img-a", name: "panel-a.png", width: 40, height: 30, image_url: "image", has_annotation: true, has_bbox: true, has_classification: true, classification: "OK", shape_count: 1, labels: ["scratch"], review_status: "pending" },
  { item_id: "img-b", name: "panel-b.png", width: 40, height: 30, image_url: "image", has_annotation: false, has_bbox: false, has_classification: false, classification: "", shape_count: 0, labels: [], review_status: "pending" },
];

const workspace = {
  manifest_id: "demo",
  manifest_name: "Inspection set",
  total: 2,
  annotated: 1,
  pending: 1,
  labels: ["scratch"],
  classification_labels: ["OK", "NG"],
  annotation_tool: "x-anylabeling",
  items,
};

async function stubApi(page: Page) {
  let manifests = [initialManifest];
  const annotations: Record<string, { version: string; flags: Record<string, unknown>; shapes: unknown[] }> = {
    "img-a": { version: "2.4.0", flags: {}, shapes: [] },
    "img-b": { version: "2.4.0", flags: {}, shapes: [] },
  };
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    if (path === "/api/session") return route.fulfill({ json: { token: "test-token" } });
    if (path === "/api/manifests" && method === "GET") return route.fulfill({ json: { manifests } });
    if (path === "/api/manifests" && method === "POST") {
      const created = { manifest_id: "new-set", name: "New set", source_type: "folder", created_at: "2026-01-01", item_count: 2 };
      manifests = [created, ...manifests];
      return route.fulfill({ status: 201, json: created });
    }
    if (path === "/api/workspace" && method === "POST") return route.fulfill({ json: { ...workspace, manifest_id: "new-set", manifest_name: "New set" } });
    if (path.endsWith("/exports") && method === "GET") return route.fulfill({ json: { exports: [] } });
    if (path.endsWith("/items/img-a") && method === "GET") return route.fulfill({ json: { item: items[0], annotation: annotations["img-a"] } });
    if (path.endsWith("/items/img-b") && method === "GET") return route.fulfill({ json: { item: items[1], annotation: annotations["img-b"] } });
    if (path.endsWith("/annotation") && method === "PUT") {
      const itemId = path.includes("img-a") ? "img-a" : "img-b";
      annotations[itemId] = request.postDataJSON();
      return route.fulfill({ json: { saved: true, shape_count: annotations[itemId].shapes.length } });
    }
    if (path.endsWith("/image")) return route.fulfill({ status: 200, contentType: "image/svg+xml", body: '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="30"><rect width="40" height="30" fill="#eee"/></svg>' });
    if (path.endsWith("/review") && method === "PUT") return route.fulfill({ json: { status: "approved" } });
    if (path.endsWith("/classification") && method === "PUT") return route.fulfill({ json: { classification: "OK" } });
    return route.fulfill({ json: { ok: true } });
  });
}

test.beforeEach(async ({ page }) => {
  await stubApi(page);
  await page.goto("/");
});

test("restores the selected dataset and supports reversible pending filters", async ({ page }) => {
  await expect(page.getByRole("heading", { name: "標注工作台" })).toBeVisible();
  await expect(page.getByRole("button", { name: /panel-a\.png/ })).toBeVisible();
  await page.getByRole("combobox", { name: "影像篩選" }).selectOption("pending");
  await expect(page.getByRole("button", { name: /panel-a\.png/ })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /panel-b\.png/ })).toBeVisible();
  await page.getByRole("button", { name: "清除", exact: true }).click();
  await expect(page.getByRole("button", { name: /panel-a\.png/ })).toBeVisible();
});

test("records an approval decision in the review workflow", async ({ page }) => {
  await page.getByRole("button", { name: /審核$/ }).click();
  await expect(page.getByRole("heading", { name: "審核決策" })).toBeVisible();
  await page.getByRole("button", { name: /核准標注/ }).click();
  await expect(page.getByText("已核准這張圖片")).toBeVisible();
});

test("draws, saves, and reopens a rectangle annotation", async ({ page }) => {
  const canvas = page.getByLabel("圖片與標注框");
  await expect(canvas).toBeVisible();
  const bounds = await canvas.boundingBox();
  expect(bounds).not.toBeNull();
  if (!bounds) return;
  await page.mouse.move(bounds.x + 8, bounds.y + 8);
  await page.mouse.down();
  await page.mouse.move(bounds.x + bounds.width * 0.7, bounds.y + bounds.height * 0.7);
  await page.mouse.up();
  await expect(page.getByLabel("第 1 個框選類別")).toBeVisible();
  await page.getByRole("button", { name: /保存標注/ }).click();
  await page.getByRole("button", { name: /panel-b\.png/ }).click();
  await page.getByRole("button", { name: /panel-a\.png/ }).click();
  await expect(page.getByLabel("第 1 個框選類別")).toBeVisible();
  await expect(page.getByText("已同步")).toBeVisible();
});

test("creates a local folder dataset and opens the workbench", async ({ page }) => {
  await page.getByRole("navigation", { name: "工作流程" }).getByRole("button", { name: /資料集/ }).click();
  await page.getByLabel("資料夾路徑").fill("C:\\data\\inspection");
  await page.getByLabel("資料集名稱").fill("New set");
  await page.getByRole("button", { name: /建立資料集並開始標注/ }).click();
  await expect(page.getByRole("heading", { name: "標注工作台" })).toBeVisible();
});
