import { expect, test } from "@playwright/test";
import { createHash } from "node:crypto";
import { spawn, type ChildProcess } from "node:child_process";
import { once } from "node:events";
import { cp, mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { createServer, type AddressInfo } from "node:net";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const E2E_DIR = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(E2E_DIR, "../..");
const WEB_DIST = resolve(REPO_ROOT, "web/dist");
const PNG_FIXTURE = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAEAAAAAwCAIAAAAuKetIAAAAaElEQVR4nNXOQREAIAzAsFL1aEELohCxB9coyNrnUiZxEidxEidxEidxEidxEidxEidxEidxEidxEidxEidxEidxEidxEidxEidxEidxEidxEidxEidxEidxEidxEidxEufvwNQDBTECXnXy1N4AAAAASUVORK5CYII=",
  "base64",
);

let serviceProcess: ChildProcess | undefined;
let appDataDir = "";
let sourceDir = "";
let exportDir = "";
let servicePort = 0;
let temporaryRoot = "";
let serviceLogs = "";
const temporaryRoots: string[] = [];

async function freeLoopbackPort(): Promise<number> {
  const probe = createServer();
  await new Promise<void>((resolveListen, reject) => {
    probe.once("error", reject);
    probe.listen(0, "127.0.0.1", resolveListen);
  });
  const port = (probe.address() as AddressInfo).port;
  await new Promise<void>((resolveClose, reject) => {
    probe.close((error) => error ? reject(error) : resolveClose());
  });
  return port;
}

async function startLocalService(): Promise<ChildProcess> {
  const pythonOverride = process.env.ANNOTATION_PYTHON;
  const executable = pythonOverride ?? (process.platform === "win32" ? "py" : "python3");
  const pythonVersionArgs = !pythonOverride && process.platform === "win32" ? ["-3.14"] : [];
  const child = spawn(executable, [
    ...pythonVersionArgs,
    "-m", "annotation_web.server",
    "--host", "127.0.0.1",
    "--port", String(servicePort),
    "--app-data-dir", appDataDir,
    "--web-dir", WEB_DIST,
  ], { cwd: REPO_ROOT, stdio: ["ignore", "ignore", "pipe"] });
  let logs = "";
  child.stderr?.setEncoding("utf8");
  child.stderr?.on("data", (chunk: string) => { logs += chunk; });
  child.once("error", (error) => { logs += `${error.message}\n`; });

  const deadline = Date.now() + 20_000;
  while (Date.now() < deadline) {
    if (child.exitCode !== null || child.signalCode !== null) {
      throw new Error(`Local annotation service exited before becoming ready.\n${logs}`);
    }
    try {
      const response = await fetch(`http://127.0.0.1:${servicePort}/api/health`, { signal: AbortSignal.timeout(500) });
      if (response.ok) {
        serviceLogs += logs;
        return child;
      }
    } catch {
      // The loopback process is still starting.
    }
    await new Promise((resolveDelay) => setTimeout(resolveDelay, 100));
  }
  child.kill();
  serviceLogs += logs;
  throw new Error(`Timed out starting the local annotation service.\n${logs}`);
}

async function stopLocalService(): Promise<void> {
  const child = serviceProcess;
  serviceProcess = undefined;
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  child.kill();
  let timeout: NodeJS.Timeout | undefined;
  await Promise.race([
    once(child, "exit"),
    new Promise((resolveDelay) => { timeout = setTimeout(resolveDelay, 5_000); }),
  ]);
  if (timeout) clearTimeout(timeout);
  if (child.exitCode === null && child.signalCode === null) child.kill("SIGKILL");
}

test("runs a real local dataset through annotation, review, export, and restart", async ({ page }) => {
  test.setTimeout(60_000);
  temporaryRoot = await mkdtemp(join(tmpdir(), "annotation-local-service-e2e-"));
  temporaryRoots.push(temporaryRoot);
  appDataDir = join(temporaryRoot, "app data");
  sourceDir = join(temporaryRoot, "資料集 with spaces");
  exportDir = join(temporaryRoot, "exports with spaces");
  await mkdir(join(sourceDir, "nested folder"), { recursive: true });
  await writeFile(join(sourceDir, "panel-a.png"), PNG_FIXTURE);
  await writeFile(join(sourceDir, "nested folder", "panel-b.png"), PNG_FIXTURE);
  servicePort = await freeLoopbackPort();
  serviceProcess = await startLocalService();
  const origin = `http://127.0.0.1:${servicePort}`;
  const originalImageHash = createHash("sha256").update(await readFile(join(sourceDir, "panel-a.png"))).digest("hex");

  await page.goto(origin);
  await expect(page.getByRole("navigation", { name: "工作流程" })).toBeVisible();
  await page.getByRole("navigation", { name: "工作流程" }).getByRole("button", { name: /資料集/ }).click();
  await page.getByLabel("資料夾路徑").fill(sourceDir);
  await page.getByLabel("資料集名稱").fill("Local browser integration");
  await page.getByRole("button", { name: /建立資料集並開始標注/ }).click();

  await expect(page.getByRole("heading", { name: "標注工作台" })).toBeVisible();
  await expect(page.getByRole("button", { name: /panel-a\.png/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /panel-b\.png/ })).toBeVisible();
  const workflow = page.getByRole("navigation", { name: "工作流程" });
  await workflow.getByRole("button", { name: "資料集" }).click();
  await page.getByRole("button", { name: "標籤管理" }).click();
  await page.getByLabel("框選標籤").fill("scratch");
  await page.getByLabel("整張圖片分類").fill("OK\nNG");
  await page.getByLabel("外部標注工具").selectOption("labelme");
  await page.getByRole("button", { name: /保存工作區設定/ }).click();
  await expect(page.getByText("工作區設定已保存")).toBeVisible();
  await workflow.getByRole("button", { name: "標注工作台" }).click();
  await page.getByRole("button", { name: /panel-a\.png/ }).click();
  const canvas = page.getByLabel("圖片與標注框");
  await expect(canvas).toBeVisible();
  const bounds = await canvas.boundingBox();
  expect(bounds).not.toBeNull();
  if (!bounds) throw new Error("Annotation canvas has no visible bounds");
  await page.mouse.move(bounds.x + bounds.width * 0.2, bounds.y + bounds.height * 0.2);
  await page.mouse.down();
  await page.mouse.move(bounds.x + bounds.width * 0.8, bounds.y + bounds.height * 0.8);
  await page.mouse.up();
  await expect(page.getByLabel("第 1 個框選類別")).toBeVisible();
  const sidecarPath = join(sourceDir, "panel-a.json");
  await page.getByRole("button", { name: /保存標注/ }).click();
  await expect(page.getByText("已同步")).toBeVisible();
  await page.getByLabel("分類結果").selectOption("OK");
  await expect(page.getByRole("button", { name: /panel-a\.png/ })).toContainText("OK");
  const initialAnnotation = JSON.parse(await readFile(sidecarPath, "utf8")) as { shapes: Array<{ points: number[][] }> };
  const initialPoints = initialAnnotation.shapes[0].points;

  const selectTool = page.getByRole("group", { name: "標注工具" }).getByRole("button", { name: /選取/ });
  await selectTool.click();
  const canvasMetrics = await canvas.evaluate((element: HTMLCanvasElement) => {
    const rect = element.getBoundingClientRect();
    return { width: rect.width, height: rect.height };
  });
  const moveX = canvasMetrics.width * 0.04;
  const moveY = canvasMetrics.height * 0.04;
  await page.mouse.move(bounds.x + bounds.width * 0.5, bounds.y + bounds.height * 0.5);
  await page.mouse.down();
  await page.mouse.move(bounds.x + bounds.width * 0.5 + moveX, bounds.y + bounds.height * 0.5 + moveY);
  await page.mouse.up();
  const beforeResize = await page.locator(".box-coordinates").innerText();

  const resizedBounds = await canvas.boundingBox();
  expect(resizedBounds).not.toBeNull();
  if (!resizedBounds) throw new Error("Annotation canvas has no visible bounds after moving a rectangle");
  await page.mouse.move(resizedBounds.x + resizedBounds.width * 0.84, resizedBounds.y + resizedBounds.height * 0.84);
  await page.mouse.down();
  await page.mouse.move(resizedBounds.x + resizedBounds.width * 0.90, resizedBounds.y + resizedBounds.height * 0.90);
  await page.mouse.up();
  const afterResize = await page.locator(".box-coordinates").innerText();
  expect(afterResize).not.toBe(beforeResize);
  await page.getByRole("button", { name: "↶ 復原" }).click();
  await expect(page.locator(".box-coordinates")).toHaveText(beforeResize);
  await page.getByRole("button", { name: "↷ 重做" }).click();
  await expect(page.locator(".box-coordinates")).toHaveText(afterResize);

  await page.getByRole("button", { name: "刪除第 1 個框選" }).click();
  await expect(page.getByLabel("第 1 個框選類別")).toHaveCount(0);
  await page.getByRole("button", { name: "↶ 復原" }).click();
  await expect(page.getByLabel("第 1 個框選類別")).toBeVisible();
  await page.getByRole("button", { name: "↷ 重做" }).click();
  await expect(page.getByLabel("第 1 個框選類別")).toHaveCount(0);
  await page.getByRole("button", { name: "↶ 復原" }).click();
  await expect(page.getByLabel("第 1 個框選類別")).toBeVisible();

  await page.getByRole("button", { name: /保存標注/ }).click();
  await expect(page.getByText("已同步")).toBeVisible();

  await page.getByRole("button", { name: /panel-b\.png/ }).click();
  await page.getByRole("button", { name: /panel-a\.png/ }).click();
  await expect(page.getByLabel("第 1 個框選類別")).toBeVisible();
  const savedAnnotation = JSON.parse(await readFile(sidecarPath, "utf8")) as { shapes: Array<{ shape_type: string; points: number[][] }> };
  expect(savedAnnotation.shapes).toHaveLength(1);
  expect(savedAnnotation.shapes[0].shape_type).toBe("rectangle");
  expect(savedAnnotation.shapes[0].points).toHaveLength(2);
  expect(savedAnnotation.shapes[0].points[0][0]).toBeGreaterThan(0);
  expect(savedAnnotation.shapes[0].points[0][0]).toBeGreaterThan(initialPoints[0][0]);
  expect(savedAnnotation.shapes[0].points[0][1]).toBeGreaterThan(initialPoints[0][1]);
  expect(savedAnnotation.shapes[0].points[1][0]).toBeGreaterThan(savedAnnotation.shapes[0].points[0][0]);
  expect(savedAnnotation.shapes[0].points[1][1]).toBeGreaterThan(savedAnnotation.shapes[0].points[0][1]);
  expect(savedAnnotation.shapes[0].points[1][0]).toBeGreaterThan(initialPoints[1][0]);
  expect(savedAnnotation.shapes[0].points[1][1]).toBeGreaterThan(initialPoints[1][1]);
  expect(createHash("sha256").update(await readFile(join(sourceDir, "panel-a.png"))).digest("hex")).toBe(originalImageHash);

  await workflow.getByRole("button", { name: "審核" }).click();
  await page.getByLabel("審核備註").fill("邊界確認完成");
  await page.getByRole("button", { name: /核准標注/ }).click();
  await expect(page.getByText("已核准這張圖片")).toBeVisible();
  await page.getByLabel("我已完成必要抽查與異常處理").check();
  const savedReview = JSON.parse(await readFile(join(sourceDir, "panel-a.png.review.json"), "utf8")) as { status: string; comment: string };
  expect(savedReview).toMatchObject({ status: "approved", comment: "邊界確認完成" });

  await workflow.getByRole("button", { name: "匯出" }).click();
  await page.getByLabel("允許部分匯出").check();
  for (const format of ["YOLO txt", "Pascal VOC", "ImageFolder", "CSV"]) {
    await page.getByText(format, { exact: true }).click();
  }
  await page.getByText("啟用 Train / Val / Test 分割", { exact: true }).click();
  const splitInputs = page.locator(".split-fields input[type=number]");
  await splitInputs.nth(0).fill("100");
  await splitInputs.nth(1).fill("0");
  await splitInputs.nth(2).fill("0");
  await page.getByLabel("資料夾路徑").fill(exportDir);
  await page.getByRole("button", { name: /檢查並開始匯出/ }).click();
  await expect(page.getByRole("heading", { name: "匯出完成" })).toBeVisible();
  const cocoExport = JSON.parse(await readFile(join(exportDir, "coco_json", "train.json"), "utf8")) as {
    images: Array<{ file_name: string }>;
    annotations: Array<{ bbox: number[] }>;
  };
  expect(cocoExport.images.map((image) => image.file_name).sort()).toEqual(["panel-a.png", "panel-b.png"]);
  expect(cocoExport.annotations).toHaveLength(1);
  expect(cocoExport.annotations[0].bbox[2]).toBeGreaterThan(0);
  expect(await readFile(join(exportDir, "yolo_txt", "labels", "train", "panel-a.txt"), "utf8")).toMatch(/^0\s/);
  expect(await readFile(join(exportDir, "pascal_voc", "Annotations", "panel-a.xml"), "utf8")).toContain("<name>scratch</name>");
  expect(await readFile(join(exportDir, "pascal_voc", "ImageSets", "Main", "train.txt"), "utf8")).toContain("panel-a");
  expect(await readFile(join(exportDir, "imagefolder", "train", "OK", "panel-a.png"))).toBeTruthy();
  const csvExport = await readFile(join(exportDir, "csv", "annotations.csv"), "utf8");
  expect(csvExport).toContain(",train,OK,scratch,rectangle,");

  await page.getByLabel("資料夾路徑").fill(join(temporaryRoot, "invalid|destination"));
  await page.getByRole("button", { name: /檢查並開始匯出/ }).click();
  await expect(page.getByRole("heading", { name: "匯出檢查結果" })).toBeVisible();
  await expect(page.getByText(/Export failed/)).toBeVisible();
  for (const format of ["coco_json", "yolo_txt", "pascal_voc", "imagefolder", "csv"]) {
    await expect(page.getByText(format, { exact: true })).toBeVisible();
  }

  await workflow.getByRole("button", { name: "審核" }).click();
  await page.getByRole("button", { name: /panel-b\.png/ }).click();
  await page.getByLabel("審核備註").fill("需補標後重送");
  await page.getByRole("button", { name: "退回修正" }).click();
  await expect(page.getByText("已退回這張圖片")).toBeVisible();
  const rejectedReview = JSON.parse(await readFile(join(sourceDir, "nested folder", "panel-b.png.review.json"), "utf8")) as { status: string; comment: string };
  expect(rejectedReview).toMatchObject({ status: "rejected", comment: "需補標後重送" });

  await stopLocalService();
  serviceProcess = await startLocalService();
  await page.reload();
  await expect(page.getByRole("heading", { name: "標注工作台" })).toBeVisible();
  await expect(page.getByRole("button", { name: /panel-a\.png/ })).toBeVisible();
  await workflow.getByRole("button", { name: "資料集" }).click();
  await page.getByRole("button", { name: "標籤管理" }).click();
  await expect(page.getByLabel("外部標注工具")).toHaveValue("labelme");
  await workflow.getByRole("button", { name: "標注工作台" }).click();
  await page.getByRole("button", { name: /panel-a\.png/ }).click();
  await expect(page.getByLabel("第 1 個框選類別")).toBeVisible();
  await expect(page.getByLabel("分類結果")).toHaveValue("OK");
  const restoredPanel = page.getByRole("button", { name: /panel-a\.png/ });
  await expect(restoredPanel).toContainText("OK");
  await expect(restoredPanel).toContainText("已核准");
  await expect(page.getByText("已同步")).toBeVisible();
  await workflow.getByRole("button", { name: "審核" }).click();
  await expect(page.getByLabel("審核備註")).toHaveValue("邊界確認完成");
  await page.getByLabel("影像篩選").selectOption("rejected");
  await expect(page.getByRole("button", { name: /panel-b\.png/ })).toContainText("已退回");
  await expect(page.getByRole("button", { name: /panel-a\.png/ })).toHaveCount(0);
  await page.getByRole("button", { name: /panel-b\.png/ }).click();
  await expect(page.getByLabel("審核備註")).toHaveValue("需補標後重送");
  await page.getByRole("button", { name: "清除", exact: true }).click();
  await workflow.getByRole("button", { name: "匯出" }).click();
  for (const format of ["coco_json", "yolo_txt", "pascal_voc", "imagefolder", "csv"]) {
    await expect(page.getByText(format, { exact: true })).toBeVisible();
  }
  expect(createHash("sha256").update(await readFile(join(sourceDir, "panel-a.png"))).digest("hex")).toBe(originalImageHash);

  await workflow.getByRole("button", { name: "標注工作台" }).click();
  await page.getByRole("button", { name: /panel-a\.png/ }).click();
  const clearClassificationResponse = page.waitForResponse((response) =>
    response.request().method() === "PUT"
    && new URL(response.url()).pathname.endsWith("/classification"),
  );
  await page.getByLabel("分類結果").selectOption("");
  expect((await clearClassificationResponse).status()).toBe(200);
  await expect(page.getByLabel("分類結果")).toHaveValue("");
  await expect(page.getByRole("button", { name: /panel-a\.png/ })).not.toContainText("OK");
  await page.getByRole("button", { name: /panel-b\.png/ }).click();
  await page.getByRole("button", { name: /panel-a\.png/ }).click();
  await expect(page.getByLabel("分類結果")).toHaveValue("");

  await stopLocalService();
  serviceProcess = await startLocalService();
  await page.reload();
  await expect(page.getByRole("heading", { name: "標注工作台" })).toBeVisible();
  await page.getByRole("button", { name: /panel-a\.png/ }).click();
  await expect(page.getByLabel("分類結果")).toHaveValue("");
  await expect(page.getByRole("button", { name: /panel-a\.png/ })).not.toContainText("OK");
});

test("renders every supported local image format and honors recursive scanning", async ({ page }) => {
  test.setTimeout(60_000);
  temporaryRoot = await mkdtemp(join(tmpdir(), "annotation-format-matrix-e2e-"));
  temporaryRoots.push(temporaryRoot);
  appDataDir = join(temporaryRoot, "app data");
  sourceDir = join(temporaryRoot, "format dataset");
  const fixtureDir = join(E2E_DIR, "fixtures", "image-format-matrix");
  await cp(fixtureDir, sourceDir, { recursive: true });
  const fileNames = [
    "root-0.jpg",
    "root-1.jpeg",
    "root-2.png",
    "root-3.bmp",
    "root-4.webp",
    "root-5.tif",
    "root-6.tiff",
    "UPPER.PNG",
    join("nested", "nested.jpeg"),
    "ignore.gif",
    "notes.txt",
  ];
  const originalHashes = new Map(
    await Promise.all(fileNames.map(async (name) => [
      name,
      createHash("sha256").update(await readFile(join(sourceDir, name))).digest("hex"),
    ] as const)),
  );
  servicePort = await freeLoopbackPort();
  serviceProcess = await startLocalService();
  await page.goto(`http://127.0.0.1:${servicePort}`);
  const workflow = page.getByRole("navigation", { name: "工作流程" });
  await workflow.getByRole("button", { name: "資料集" }).click();
  await page.getByLabel("資料夾路徑").fill(sourceDir);
  await page.getByLabel("資料集名稱").fill("Recursive image format matrix");
  await page.getByRole("checkbox", { name: "包含子資料夾" }).check();
  await page.getByRole("button", { name: /建立資料集並開始標注/ }).click();

  await expect(page.getByRole("heading", { name: "標注工作台" })).toBeVisible();
  await expect(page.locator(".top-count")).toContainText("9 張圖片");
  for (const name of fileNames.slice(0, 9)) {
    const displayName = name.split(/[\\/]/).pop() ?? name;
    const button = page.getByRole("button", { name: new RegExp(displayName.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")) });
    await expect(button).toBeVisible();
    await button.click();
    const canvas = page.getByLabel("圖片與標注框");
    await expect(canvas).toBeVisible();
    await expect.poll(() => canvas.evaluate((element: HTMLCanvasElement) => [element.width, element.height])).toEqual([16, 12]);
  }
  await expect(page.getByRole("button", { name: /ignore\.gif/ })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /notes\.txt/ })).toHaveCount(0);
  for (const name of fileNames) {
    expect(createHash("sha256").update(await readFile(join(sourceDir, name))).digest("hex")).toBe(originalHashes.get(name));
  }

  await workflow.getByRole("button", { name: "資料集" }).click();
  await page.getByLabel("資料夾路徑").fill(sourceDir);
  await page.getByLabel("資料集名稱").fill("Root-only image format matrix");
  await page.getByRole("checkbox", { name: "包含子資料夾" }).uncheck();
  await page.getByRole("button", { name: /建立資料集並開始標注/ }).click();
  await expect(page.getByRole("heading", { name: "標注工作台" })).toBeVisible();
  await expect(page.locator(".top-count")).toContainText("8 張圖片");
  await expect(page.getByRole("button", { name: /root-0\.jpg/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /UPPER\.PNG/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /nested\.jpeg/ })).toHaveCount(0);
  for (const name of fileNames) {
    expect(createHash("sha256").update(await readFile(join(sourceDir, name))).digest("hex")).toBe(originalHashes.get(name));
  }
});

test("previews label merge impact before the real service rewrites sidecars", async ({ page }) => {
  test.setTimeout(40_000);
  await stopLocalService();
  temporaryRoot = await mkdtemp(join(tmpdir(), "annotation-label-merge-e2e-"));
  temporaryRoots.push(temporaryRoot);
  appDataDir = join(temporaryRoot, "app data");
  sourceDir = join(temporaryRoot, "dataset");
  await mkdir(sourceDir, { recursive: true });

  const labels = [
    ["panel-a", "scratch"],
    ["panel-b", "dent"],
    ["panel-target", "stain"],
  ] as const;
  for (const [stem, label] of labels) {
    await writeFile(join(sourceDir, stem + ".png"), PNG_FIXTURE);
    await writeFile(join(sourceDir, stem + ".json"), JSON.stringify({
      version: "2.4.0",
      flags: {},
      shapes: [{
        label,
        points: [[2, 2], [24, 24]],
        group_id: null,
        description: "",
        shape_type: "rectangle",
        flags: {},
      }],
      imagePath: stem + ".png",
      imageData: null,
      imageHeight: 48,
      imageWidth: 64,
    }, null, 2));
  }
  servicePort = await freeLoopbackPort();
  serviceProcess = await startLocalService();
  const origin = "http://127.0.0.1:" + servicePort;

  await page.goto(origin);
  const workflow = page.getByRole("navigation", { name: "工作流程" });
  await workflow.getByRole("button", { name: "資料集" }).click();
  await page.getByLabel("資料夾路徑").fill(sourceDir);
  await page.getByLabel("資料集名稱").fill("Merge impact fixture");
  await page.getByRole("button", { name: /建立資料集並開始標注/ }).click();
  await expect(page.getByRole("heading", { name: "標注工作台" })).toBeVisible();
  await workflow.getByRole("button", { name: "資料集" }).click();
  await page.getByRole("button", { name: "標籤管理" }).click();
  await expect(page.getByRole("heading", { name: "標籤使用狀況" })).toBeVisible();

  await page.getByLabel("選擇要合併的標籤").selectOption(["scratch", "dent"]);
  await page.getByLabel("合併後保留的標籤").selectOption("stain");
  const preview = page.getByTestId("merge-impact-preview");
  await expect(preview).toContainText("修改 2 次標注檔案");
  await expect(preview).toContainText("2 個框與 0 個圖片分類值");
  await expect(preview).toContainText("目標「stain」目前有 1 張圖片");

  const dialogMessages: string[] = [];
  const dismissDialog = async (dialog: import("@playwright/test").Dialog) => {
    dialogMessages.push(dialog.message());
    await dialog.dismiss();
  };
  page.on("dialog", dismissDialog);
  await page.getByRole("button", { name: "合併選取標籤" }).click();
  expect(dialogMessages[0]).toContain("2 次標注檔案");
  await expect(preview).toBeVisible();
  page.off("dialog", dismissDialog);

  let confirmationMessage = "";
  page.once("dialog", async (dialog) => {
    confirmationMessage = dialog.message();
    await dialog.accept();
  });
  await page.getByRole("button", { name: "合併選取標籤" }).click();
  expect(confirmationMessage).toContain("scratch、dent");
  await expect(page.locator(".label-table-row")).toHaveCount(1);
  await expect(page.locator(".label-table-row")).toContainText("stain");

  for (const stem of ["panel-a", "panel-b", "panel-target"]) {
    const saved = JSON.parse(await readFile(join(sourceDir, stem + ".json"), "utf8")) as {
      shapes: Array<{ label: string }>;
    };
    expect(saved.shapes.map((shape) => shape.label)).toEqual(["stain"]);
  }
});

test("preserves existing polygons when React saves a rectangle edit", async ({ page }) => {
  test.setTimeout(40_000);
  await stopLocalService();
  temporaryRoot = await mkdtemp(join(tmpdir(), "annotation-polygon-preserve-e2e-"));
  temporaryRoots.push(temporaryRoot);
  appDataDir = join(temporaryRoot, "app data");
  sourceDir = join(temporaryRoot, "dataset");
  await mkdir(sourceDir, { recursive: true });
  await writeFile(join(sourceDir, "panel-a.png"), PNG_FIXTURE);
  const polygons = [
    { label: "dent", points: [[4, 4], [20, 4], [20, 20], [4, 20]], shape_type: "polygon", description: "preserve dent" },
    { label: "stain", points: [[24, 26], [36, 26], [36, 40], [24, 40]], shape_type: "polygon", description: "preserve stain" },
  ];
  await writeFile(join(sourceDir, "panel-a.json"), JSON.stringify({
    version: "2.4.0",
    imagePath: "panel-a.png",
    flags: {},
    shapes: polygons.map((shape) => ({
      ...shape,
      group_id: null,
      difficult: false,
      flags: {},
      attributes: {},
    })),
  }));

  servicePort = await freeLoopbackPort();
  serviceProcess = await startLocalService();
  await page.goto(`http://127.0.0.1:${servicePort}`);
  await expect(page.getByRole("navigation", { name: "工作流程" })).toBeVisible();
  const workflow = page.getByRole("navigation", { name: "工作流程" });
  await workflow.getByRole("button", { name: "資料集" }).click();
  await page.getByLabel("資料夾路徑").fill(sourceDir);
  await page.getByLabel("資料集名稱").fill("Polygon preservation");
  await page.getByRole("button", { name: /建立資料集並開始標注/ }).click();
  await expect(page.getByRole("button", { name: /panel-a\.png/ })).toBeVisible();

  await workflow.getByRole("button", { name: "資料集" }).click();
  await page.getByRole("button", { name: "標籤管理" }).click();
  await page.getByLabel("框選標籤").fill("scratch\ndent\nstain");
  await page.getByRole("button", { name: /保存工作區設定/ }).click();
  await expect(page.getByText("工作區設定已保存")).toBeVisible();
  await workflow.getByRole("button", { name: "標注工作台" }).click();
  await page.getByRole("button", { name: /panel-a\.png/ }).click();

  const canvas = page.getByLabel("圖片與標注框");
  await expect(canvas).toBeVisible();
  const bounds = await canvas.boundingBox();
  expect(bounds).not.toBeNull();
  if (!bounds) throw new Error("Annotation canvas has no visible bounds");
  await page.mouse.move(bounds.x + bounds.width * 0.62, bounds.y + bounds.height * 0.12);
  await page.mouse.down();
  await page.mouse.move(bounds.x + bounds.width * 0.86, bounds.y + bounds.height * 0.40);
  await page.mouse.up();
  await expect(page.getByLabel("第 1 個框選類別")).toHaveValue("scratch");
  await page.getByRole("button", { name: /保存標注/ }).click();
  await expect(page.getByText("標注已保存")).toBeVisible();

  const saved = JSON.parse(await readFile(join(sourceDir, "panel-a.json"), "utf8")) as {
    shapes: Array<{ label: string; shape_type: string; points: number[][] }>;
  };
  expect(saved.shapes.map((shape) => shape.shape_type)).toEqual(["polygon", "polygon", "rectangle"]);
  expect(saved.shapes.slice(0, 2).map(({ label, points }) => ({ label, points }))).toEqual(polygons.map(({ label, points }) => ({ label, points })));
  expect(saved.shapes[2].label).toBe("scratch");
});

test("imports and edits an LV SHA-addressed handoff through React", async ({ page }) => {
  test.setTimeout(40_000);
  await stopLocalService();
  temporaryRoot = await mkdtemp(join(tmpdir(), "annotation-lv-handoff-e2e-"));
  temporaryRoots.push(temporaryRoot);
  appDataDir = join(temporaryRoot, "app data");
  const handoffDir = join(temporaryRoot, "lv_labeling_handoff", "lv_browser_handoff");
  sourceDir = join(handoffDir, "images");
  await mkdir(sourceDir, { recursive: true });

  const imageName = `${createHash("sha256").update(PNG_FIXTURE).digest("hex")}.png`;
  const imagePath = join(sourceDir, imageName);
  const sidecarPath = join(sourceDir, imageName.replace(/\.png$/i, ".json"));
  await writeFile(imagePath, PNG_FIXTURE);
  const originalImageHash = createHash("sha256").update(await readFile(imagePath)).digest("hex");
  const seededAnnotation = {
    version: "0.3.3",
    flags: {},
    shapes: [{
      label: "old-class",
      points: [[4, 5], [30, 35]],
      group_id: null,
      shape_type: "rectangle",
      flags: {},
      score: 0.9,
    }],
    imagePath: imageName,
    imageData: null,
    imageHeight: 48,
    imageWidth: 64,
  };
  const seededSidecar = JSON.stringify(seededAnnotation, null, 2);
  await writeFile(sidecarPath, seededSidecar);
  await writeFile(join(handoffDir, "classes.txt"), "old-class\nnew-class\n");
  await writeFile(join(handoffDir, "_handoff.json"), JSON.stringify({
    handoff_id: "lv_browser_handoff",
    version: 1,
    source: "lv-react",
    task: "re-label",
    class_options: ["old-class", "new-class"],
    items: [{
      sha256: imageName.replace(/\.png$/i, ""),
      image: `images/${imageName}`,
      filename: "panel-a.png",
      original_label: "old-class",
      seed_sha: createHash("sha256").update(seededSidecar).digest("hex"),
    }],
  }, null, 2));

  servicePort = await freeLoopbackPort();
  serviceProcess = await startLocalService();
  await page.goto(`http://127.0.0.1:${servicePort}`);
  const workflow = page.getByRole("navigation", { name: "工作流程" });
  await workflow.getByRole("button", { name: "資料集" }).click();
  await page.getByLabel("資料夾路徑").fill(sourceDir);
  await page.getByLabel("資料集名稱").fill("LV handoff import");
  await page.getByRole("button", { name: /建立資料集並開始標注/ }).click();
  await expect(page.getByRole("heading", { name: "標注工作台" })).toBeVisible();
  const imageButton = page.getByRole("button", { name: new RegExp(imageName.replace(".png", "\\.png")) });
  await expect(imageButton).toBeVisible();
  await imageButton.click();
  await expect(page.getByLabel("第 1 個框選類別")).toHaveValue("old-class");
  await expect(page.locator(".box-coordinates")).toHaveText("26 × 30");

  const canvas = page.getByLabel("圖片與標注框");
  await expect(canvas).toBeVisible();
  const bounds = await canvas.boundingBox();
  if (!bounds) throw new Error("LV handoff annotation canvas has no visible bounds");
  await page.mouse.move(bounds.x + bounds.width * 0.27, bounds.y + bounds.height * 0.42);
  await page.mouse.down();
  await page.mouse.move(bounds.x + bounds.width * 0.31, bounds.y + bounds.height * 0.46);
  await page.mouse.up();
  await page.getByRole("button", { name: /保存標注/ }).click();
  await expect(page.getByText("已同步")).toBeVisible();

  const savedBeforeRestart = JSON.parse(await readFile(sidecarPath, "utf8")) as {
    shapes: Array<{ label: string; shape_type: string; points: number[][] }>;
  };
  expect(savedBeforeRestart.shapes).toHaveLength(1);
  expect(savedBeforeRestart.shapes[0].label).toBe("old-class");
  expect(savedBeforeRestart.shapes[0].shape_type).toBe("rectangle");
  expect(savedBeforeRestart.shapes[0].points[0][0]).toBeGreaterThan(4);
  expect(savedBeforeRestart.shapes[0].points[0][1]).toBeGreaterThan(5);
  expect(createHash("sha256").update(await readFile(imagePath)).digest("hex")).toBe(originalImageHash);

  await stopLocalService();
  serviceProcess = await startLocalService();
  await page.reload();
  await page.getByRole("button", { name: new RegExp(imageName.replace(".png", "\\.png")) }).click();
  await expect(page.getByLabel("第 1 個框選類別")).toHaveValue("old-class");
  const savedAfterRestart = JSON.parse(await readFile(sidecarPath, "utf8")) as {
    shapes: Array<{ points: number[][] }>;
  };
  expect(savedAfterRestart.shapes[0].points).toEqual(savedBeforeRestart.shapes[0].points);
});

test.afterAll(async () => {
  await stopLocalService();
  for (const root of temporaryRoots) await rm(root, { recursive: true, force: true });
  if (serviceLogs) process.stderr.write(serviceLogs);
});
