import { expect, test } from "@playwright/test";
import { createHash } from "node:crypto";
import { spawn, type ChildProcess } from "node:child_process";
import { once } from "node:events";
import { cp, mkdtemp, mkdir, readFile, readdir, rm, writeFile } from "node:fs/promises";
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

  const splitExportDir = join(temporaryRoot, "exports train-val split");
  await page.getByLabel("依分類分層").uncheck();
  await splitInputs.nth(0).fill("50");
  await splitInputs.nth(1).fill("50");
  await splitInputs.nth(2).fill("0");
  await page.getByLabel("資料夾路徑").fill(splitExportDir);
  const splitExportResponse = page.waitForResponse((response) =>
    response.request().method() === "POST"
    && new URL(response.url()).pathname.endsWith("/exports"),
  );
  await page.getByRole("button", { name: /檢查並開始匯出/ }).click();
  const splitExportResult = await splitExportResponse;
  expect(splitExportResult.status()).toBe(200);
  expect((await splitExportResult.json()).mode).toBe("done");
  await expect(page.getByRole("heading", { name: "匯出完成" })).toBeVisible();
  const splitCoco = await Promise.all(["train", "val", "test"].map(async (splitName) => ({
    splitName,
    data: JSON.parse(await readFile(join(splitExportDir, "coco_json", `${splitName}.json`), "utf8")) as {
      images: Array<{ file_name: string }>;
    },
  })));
  expect(splitCoco.map(({ splitName, data }) => [splitName, data.images.length])).toEqual([
    ["train", 1], ["val", 1], ["test", 0],
  ]);
  expect(splitCoco.flatMap(({ data }) => data.images.map((image) => image.file_name)).sort())
    .toEqual(["panel-a.png", "panel-b.png"]);

  for (const boundaryCase of [
    { name: "validation-only", ratios: [0, 100, 0], expectedCounts: [0, 2, 0] },
    { name: "test-only", ratios: [0, 0, 100], expectedCounts: [0, 0, 2] },
  ]) {
    const boundaryExportDir = join(temporaryRoot, `exports ${boundaryCase.name}`);
    for (const [index, ratio] of boundaryCase.ratios.entries()) {
      await splitInputs.nth(index).fill(String(ratio));
    }
    await page.getByLabel("資料夾路徑").fill(boundaryExportDir);
    const boundaryExportResponse = page.waitForResponse((response) =>
      response.request().method() === "POST"
      && new URL(response.url()).pathname.endsWith("/exports"),
    );
    await page.getByRole("button", { name: /檢查並開始匯出/ }).click();
    const boundaryExportResult = await boundaryExportResponse;
    expect(boundaryExportResult.status()).toBe(200);
    expect((await boundaryExportResult.json()).mode).toBe("done");
    const boundaryCoco = await Promise.all(["train", "val", "test"].map(async (splitName) => ({
      splitName,
      data: JSON.parse(await readFile(join(boundaryExportDir, "coco_json", `${splitName}.json`), "utf8")) as {
        images: Array<{ file_name: string }>;
      },
    })));
    expect(boundaryCoco.map(({ splitName, data }) => [splitName, data.images.length])).toEqual([
      ["train", boundaryCase.expectedCounts[0]],
      ["val", boundaryCase.expectedCounts[1]],
      ["test", boundaryCase.expectedCounts[2]],
    ]);
    expect(boundaryCoco.flatMap(({ data }) => data.images.map((image) => image.file_name)).sort())
      .toEqual(["panel-a.png", "panel-b.png"]);
  }

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
  await expect(page.getByLabel("審核備註")).toHaveValue("需補標後重送");
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
  const recentExports = page.locator(".export-aside .history-row");
  await expect(recentExports).toHaveCount(5);
  expect(new Set(await recentExports.locator("strong").allTextContents())).toEqual(
    new Set(["coco_json", "yolo_txt", "pascal_voc", "imagefolder", "csv"]),
  );
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

test("imports a local YOLO model through React into app data without exposing its source path", async ({ page }) => {
  test.setTimeout(60_000);
  await stopLocalService();
  temporaryRoot = await mkdtemp(join(tmpdir(), "annotation-local-model-e2e-"));
  temporaryRoots.push(temporaryRoot);
  appDataDir = join(temporaryRoot, "app data");
  sourceDir = join(temporaryRoot, "model dataset");
  await mkdir(sourceDir, { recursive: true });
  await writeFile(join(sourceDir, "panel-a.png"), PNG_FIXTURE);
  servicePort = await freeLoopbackPort();
  serviceProcess = await startLocalService();

  await page.goto(`http://127.0.0.1:${servicePort}`);
  await page.getByRole("navigation", { name: "工作流程" }).getByRole("button", { name: "資料集" }).click();
  await page.getByLabel("資料夾路徑").fill(sourceDir);
  await page.getByLabel("資料集名稱").fill("Local model import");
  await page.getByRole("button", { name: /建立資料集並開始標注/ }).click();
  await expect(page.getByRole("heading", { name: "標注工作台" })).toBeVisible();

  const modelBytes = Buffer.from("small local YOLO model fixture");
  const modelHash = createHash("sha256").update(modelBytes).digest("hex");
  await page.getByLabel("匯入本機 YOLO 模型").setInputFiles({
    name: "inspection-yolo.pt",
    mimeType: "application/octet-stream",
    buffer: modelBytes,
  });
  const modelField = page.getByRole("textbox", { name: "模型檔案路徑" });
  await expect(modelField).toHaveValue("inspection-yolo.pt");
  expect(await modelField.inputValue()).not.toContain(temporaryRoot);
  await expect(page.getByText(/本機模型 inspection-yolo\.pt 已保存至 ANnoTation App data/)).toBeVisible();
  expect(await readFile(join(appDataDir, "models", "yolo", `${modelHash}.pt`))).toEqual(modelBytes);
});

test("runs real local YOLO CPU pre-labeling through the NativeApp same-origin React host", async ({ page }) => {
  const modelPath = process.env.ANNOTATION_E2E_YOLO_MODEL;
  const imagePath = process.env.ANNOTATION_E2E_YOLO_IMAGE;
  test.skip(!modelPath || !imagePath, "Set ANNOTATION_E2E_YOLO_MODEL and ANNOTATION_E2E_YOLO_IMAGE to run real model inference");
  test.setTimeout(180_000);
  if (!modelPath || !imagePath) return;

  await stopLocalService();
  temporaryRoot = await mkdtemp(join(tmpdir(), "annotation-real-yolo-e2e-"));
  temporaryRoots.push(temporaryRoot);
  appDataDir = join(temporaryRoot, "app data");
  sourceDir = join(temporaryRoot, "model dataset");
  await mkdir(sourceDir, { recursive: true });
  const imageName = imagePath.split(/[\\/]/).at(-1) || "inference-image.jpg";
  const imageBytes = await readFile(imagePath);
  await writeFile(join(sourceDir, imageName), imageBytes);
  const modelBytes = await readFile(modelPath);
  const modelHash = createHash("sha256").update(modelBytes).digest("hex");
  servicePort = await freeLoopbackPort();
  serviceProcess = await startLocalService();
  const origin = `http://127.0.0.1:${servicePort}`;
  const apiOrigins: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname.startsWith("/api/")) apiOrigins.push(url.origin);
  });
  await page.addInitScript((selectedPath: string) => {
    Object.defineProperty(window, "NATIVEAPP_LOCAL_API", { configurable: true, value: "" });
    Object.defineProperty(window, "cimHost", {
      configurable: true,
      value: {
        chooseFile: async (options: { properties: string[] }) => {
          const hostWindow = window as Window & { __nativeAppPickerCalls?: string[][] };
          (hostWindow.__nativeAppPickerCalls ??= []).push(options.properties);
          return { canceled: false, paths: [selectedPath] };
        },
      },
    });
  }, sourceDir);

  await page.goto(origin);
  await page.getByRole("navigation", { name: "工作流程" }).getByRole("button", { name: "資料集" }).click();
  await page.getByRole("button", { name: "選擇影像資料夾…" }).click();
  await expect(page.getByLabel("資料夾路徑")).toHaveValue(sourceDir);
  await page.getByLabel("資料集名稱").fill("Real local YOLO inference");
  await page.getByRole("button", { name: /建立資料集並開始標注/ }).click();
  await expect(page.getByRole("heading", { name: "標注工作台" })).toBeVisible();
  expect(await page.evaluate(() => (window as Window & { __nativeAppPickerCalls?: string[][] }).__nativeAppPickerCalls))
    .toEqual([["openDirectory"]]);

  await page.getByLabel("匯入本機 YOLO 模型").setInputFiles({
    name: modelPath.split(/[\\/]/).at(-1) || "local-model.pt",
    mimeType: "application/octet-stream",
    buffer: modelBytes,
  });
  const modelField = page.getByRole("textbox", { name: "模型檔案路徑" });
  await expect(modelField).toHaveValue(modelPath.split(/[\\/]/).at(-1) || "local-model.pt");
  expect(await modelField.inputValue()).not.toContain(temporaryRoot);
  await expect(page.getByText(/本機模型 .* 已保存至 ANnoTation App data/)).toBeVisible();
  expect(await readFile(join(appDataDir, "models", "yolo", `${modelHash}.pt`))).toEqual(modelBytes);

  await page.getByRole("button", { name: /對此圖執行/ }).click();
  const completion = page.getByText(/AI 預標注完成：1 張、\d+ 個框/);
  await expect(completion).toBeVisible({ timeout: 150_000 });
  const detectedCount = Number((await completion.textContent())?.match(/、(\d+) 個框/)?.[1] ?? 0);
  expect(detectedCount).toBeGreaterThan(0);

  const sidecar = JSON.parse(await readFile(join(sourceDir, imageName.replace(/\.[^.]+$/, ".json")), "utf8")) as {
    imageHeight: number;
    imageWidth: number;
    shapes: Array<{ label: string; score: number; shape_type: string }>;
  };
  expect(sidecar.imageHeight).toBeGreaterThan(0);
  expect(sidecar.imageWidth).toBeGreaterThan(0);
  expect(sidecar.shapes).toHaveLength(detectedCount);
  expect(sidecar.shapes.every((shape) => shape.label && shape.score > 0 && shape.shape_type === "rectangle")).toBe(true);
  expect(apiOrigins.length).toBeGreaterThan(0);
  expect(new Set(apiOrigins)).toEqual(new Set([origin]));
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
  const secondImageBytes = await page.evaluate(() => {
    const canvas = document.createElement("canvas");
    canvas.width = 64;
    canvas.height = 48;
    const context = canvas.getContext("2d");
    if (!context) throw new Error("Could not create the duplicate-review image fixture");
    context.fillStyle = "#255f9b";
    context.fillRect(0, 0, canvas.width, canvas.height);
    return canvas.toDataURL("image/png").split(",")[1];
  }).then((value) => Buffer.from(value, "base64"));
  const secondImageName = `${createHash("sha256").update(secondImageBytes).digest("hex")}.png`;
  const imagePath = join(sourceDir, imageName);
  const secondImagePath = join(sourceDir, secondImageName);
  const sidecarPath = join(sourceDir, imageName.replace(/\.png$/i, ".json"));
  await writeFile(imagePath, PNG_FIXTURE);
  await writeFile(secondImagePath, secondImageBytes);
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
    source: "duplicate",
    task: "verify",
    instructions: "Review the pair; keep or drop decisions are human-only.",
    class_options: ["old-class", "new-class"],
    payload: {
      kind: "duplicate_pair_review",
      method: "phash",
      cross_split_only: true,
      pairs: [{
        left_sha256: imageName.replace(/\.png$/i, ""),
        right_sha256: secondImageName.replace(/\.png$/i, ""),
        left_filename: "panel-train.png",
        right_filename: "panel-test.png",
        left_split: "train",
        right_split: "test",
        distance: 2,
      }],
    },
    items: [{
      sha256: imageName.replace(/\.png$/i, ""),
      image: `images/${imageName}`,
      filename: "panel-a.png",
      original_label: "old-class",
      seed_sha: createHash("sha256").update(seededSidecar).digest("hex"),
    }, {
      sha256: secondImageName.replace(/\.png$/i, ""),
      image: `images/${secondImageName}`,
      filename: "panel-b.png",
      original_label: "old-class",
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
  const pairContext = page.getByTestId("handoff-review-context");
  await expect(pairContext).toBeVisible();
  const pairRow = page.getByTestId("handoff-review-pair");
  await expect(pairRow).toContainText("panel-train.png");
  await expect(pairRow).toContainText("panel-test.png");
  await expect(pairRow).toContainText("Hamming 2");
  await page.getByTestId("handoff-pair-right").click();
  await expect(page.locator(".crumbs strong")).toHaveText(secondImageName);
  await page.getByTestId("handoff-pair-left").click();
  await expect(page.locator(".crumbs strong")).toHaveText(imageName);
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

test("opens a local dataset through the NativeApp same-origin folder bridge", async ({ page }) => {
  test.setTimeout(40_000);
  temporaryRoot = await mkdtemp(join(tmpdir(), "annotation-nativeapp-picker-e2e-"));
  temporaryRoots.push(temporaryRoot);
  appDataDir = join(temporaryRoot, "app data");
  sourceDir = join(temporaryRoot, "選取的資料集");
  await mkdir(sourceDir, { recursive: true });
  await writeFile(join(sourceDir, "nativeapp-image.png"), PNG_FIXTURE);
  servicePort = await freeLoopbackPort();
  serviceProcess = await startLocalService();
  const origin = `http://127.0.0.1:${servicePort}`;
  const apiRequests: string[] = [];
  page.on("request", (request) => {
    if (new URL(request.url()).pathname.startsWith("/api/")) apiRequests.push(request.url());
  });
  await page.addInitScript((selectedPath: string) => {
    Object.defineProperty(window, "NATIVEAPP_LOCAL_API", { configurable: true, value: "" });
    Object.defineProperty(window, "cimHost", {
      configurable: true,
      value: { chooseFile: async () => ({ canceled: false, paths: [selectedPath] }) },
    });
  }, sourceDir);

  await page.goto(origin);
  const workflow = page.getByRole("navigation", { name: "工作流程" });
  await workflow.getByRole("button", { name: "資料集" }).click();
  await page.getByRole("button", { name: "選擇影像資料夾…" }).click();
  await expect(page.getByLabel("資料夾路徑")).toHaveValue(sourceDir);
  await page.getByLabel("資料集名稱").fill("NativeApp local dataset");
  await page.getByRole("button", { name: /建立資料集並開始標注/ }).click();

  await expect(page.getByRole("heading", { name: "標注工作台" })).toBeVisible();
  await expect(page.getByRole("button", { name: /nativeapp-image\.png/ })).toBeVisible();
  expect(apiRequests.length).toBeGreaterThan(0);
  expect(apiRequests.every((url) => url.startsWith(`${origin}/api/`))).toBe(true);
  expect(new Set(apiRequests.map((url) => new URL(url).origin))).toEqual(new Set([origin]));
});

test("exports stratified 60/20/20 splits by classification through React", async ({ page }) => {
  test.setTimeout(60_000);
  await stopLocalService();
  temporaryRoot = await mkdtemp(join(tmpdir(), "annotation-stratified-export-e2e-"));
  temporaryRoots.push(temporaryRoot);
  appDataDir = join(temporaryRoot, "app data");
  sourceDir = join(temporaryRoot, "分類資料集");
  exportDir = join(temporaryRoot, "stratified exports");
  await mkdir(sourceDir, { recursive: true });
  for (const category of ["class-a", "class-b"]) {
    for (let index = 0; index < 12; index += 1) {
      await writeFile(join(sourceDir, `${category}-${String(index).padStart(2, "0")}.png`), PNG_FIXTURE);
    }
  }

  servicePort = await freeLoopbackPort();
  serviceProcess = await startLocalService();
  const origin = `http://127.0.0.1:${servicePort}`;
  await page.goto(origin);
  const workflow = page.getByRole("navigation", { name: "工作流程" });
  await workflow.getByRole("button", { name: /資料集/ }).click();
  await page.getByLabel("資料夾路徑").fill(sourceDir);
  await page.getByLabel("資料集名稱").fill("Stratified export verification");
  await page.getByRole("button", { name: /建立資料集並開始標注/ }).click();
  await expect(page.getByRole("heading", { name: "標注工作台" })).toBeVisible();

  const seededItemCount = await page.evaluate(async () => {
    const sessionResponse = await fetch("/api/session");
    if (!sessionResponse.ok) throw new Error("Could not get the local API session token");
    const { token } = await sessionResponse.json();
    const headers = { "Content-Type": "application/json", "X-Annotation-Token": token };
    const manifestResponse = await fetch("/api/manifests", { headers });
    if (!manifestResponse.ok) throw new Error("Could not list local manifests");
    const { manifests } = await manifestResponse.json();
    const manifestId = manifests[0]?.manifest_id;
    if (!manifestId) throw new Error("The created local manifest was not found");
    const itemsResponse = await fetch(`/api/manifests/${encodeURIComponent(manifestId)}/items?view=workspace`, { headers });
    if (!itemsResponse.ok) throw new Error("Could not list local manifest items");
    const { items } = await itemsResponse.json();
    if (items.length !== 24) throw new Error(`Expected 24 local images, found ${items.length}`);
    for (const item of items) {
      const label = item.name.startsWith("class-a-") ? "class-a" : "class-b";
      const response = await fetch(
        `/api/manifests/${encodeURIComponent(manifestId)}/items/${encodeURIComponent(item.item_id)}/classification`,
        { method: "PUT", headers, body: JSON.stringify({ label }) },
      );
      if (!response.ok) throw new Error(`Could not classify ${item.name}`);
    }
    return items.length;
  });
  expect(seededItemCount).toBe(24);

  await workflow.getByRole("button", { name: "審核" }).click();
  await page.getByLabel("我已完成必要抽查與異常處理").check();
  await workflow.getByRole("button", { name: "匯出" }).click();
  await page.getByLabel("允許部分匯出").check();
  for (const format of ["YOLO txt", "Pascal VOC", "ImageFolder", "CSV"]) {
    await page.getByText(format, { exact: true }).click();
  }
  await page.getByText("啟用 Train / Val / Test 分割", { exact: true }).click();
  const splitInputs = page.locator(".split-fields input[type=number]");
  await splitInputs.nth(0).fill("60");
  await splitInputs.nth(1).fill("20");
  await splitInputs.nth(2).fill("20");
  await expect(page.getByLabel("依分類分層")).toBeChecked();
  await page.getByLabel("資料夾路徑").fill(exportDir);
  await page.getByRole("button", { name: /檢查並開始匯出/ }).click();
  await expect(page.getByRole("heading", { name: "匯出完成" })).toBeVisible();

  const splits = await Promise.all(["train", "val", "test"].map(async (splitName) => ({
    splitName,
    images: (JSON.parse(await readFile(join(exportDir, "coco_json", `${splitName}.json`), "utf8")) as {
      images: Array<{ file_name: string }>;
    }).images,
  })));
  expect(splits.map(({ splitName, images }) => [splitName, images.length])).toEqual([
    ["train", 14], ["val", 4], ["test", 6],
  ]);
  const classCounts = splits.map(({ images }) => [
    images.filter((image) => image.file_name.startsWith("class-a-")).length,
    images.filter((image) => image.file_name.startsWith("class-b-")).length,
  ]);
  expect(classCounts).toEqual([[7, 7], [2, 2], [3, 3]]);
  const expectedFilenames = Array.from({ length: 12 }, (_, index) => `class-a-${String(index).padStart(2, "0")}.png`)
    .concat(Array.from({ length: 12 }, (_, index) => `class-b-${String(index).padStart(2, "0")}.png`))
    .sort();
  const assertClassCounts = (names: string[], expected: [number, number]) => {
    expect(names.filter((name) => name.startsWith("class-a-")).length).toBe(expected[0]);
    expect(names.filter((name) => name.startsWith("class-b-")).length).toBe(expected[1]);
  };
  expect(splits.flatMap(({ images }) => images.map((image) => image.file_name)).sort()).toEqual(expectedFilenames);

  const yoloSplitNames = await Promise.all(["train", "val", "test"].map(async (splitName) =>
    readdir(join(exportDir, "yolo_txt", "images", splitName))));
  expect(yoloSplitNames.map((names) => names.length)).toEqual([14, 4, 6]);
  yoloSplitNames.forEach((names, index) => assertClassCounts(names, classCounts[index] as [number, number]));
  expect(yoloSplitNames.flat().sort()).toEqual(expectedFilenames);

  const vocSplitNames = await Promise.all(["train", "val", "test"].map(async (splitName) =>
    (await readFile(join(exportDir, "pascal_voc", "ImageSets", "Main", `${splitName}.txt`), "utf8"))
      .split(/\r?\n/).filter(Boolean).map((stem) => `${stem}.png`)));
  expect(vocSplitNames.map((names) => names.length)).toEqual([14, 4, 6]);
  vocSplitNames.forEach((names, index) => assertClassCounts(names, classCounts[index] as [number, number]));
  expect(vocSplitNames.flat().sort()).toEqual(expectedFilenames);

  const imagefolderSplitNames: string[][] = [];
  for (const [index, splitName] of ["train", "val", "test"].entries()) {
    const splitCounts = classCounts[index] as [number, number];
    const namesByClass = await Promise.all(["class-a", "class-b"].map((className) =>
      readdir(join(exportDir, "imagefolder", splitName, className))));
    for (const [classIndex, className] of ["class-a", "class-b"].entries()) {
      const names = namesByClass[classIndex];
      expect(names).toHaveLength(splitCounts[classIndex]);
      expect(names.every((name) => name.startsWith(`${className}-`))).toBe(true);
    }
    imagefolderSplitNames.push(namesByClass.flat());
  }
  expect(imagefolderSplitNames.flat().sort()).toEqual(expectedFilenames);

  const csvLines = (await readFile(join(exportDir, "csv", "annotations.csv"), "utf8"))
    .trim().split(/\r?\n/);
  const csvHeaders = csvLines[0].split(",");
  const csvRows = csvLines.slice(1).map((line) => line.split(","));
  const splitColumn = csvHeaders.indexOf("split");
  const classificationColumn = csvHeaders.indexOf("classification");
  const filePathColumn = csvHeaders.indexOf("file_path");
  expect(csvRows).toHaveLength(24);
  expect([splitColumn, classificationColumn, filePathColumn].every((column) => column >= 0)).toBe(true);
  const csvSplitNames = ["train", "val", "test"].map((splitName) => csvRows
    .filter((row) => row[splitColumn] === splitName)
    .map((row) => row[filePathColumn].split(/[\\/]/).pop() ?? ""));
  expect(csvSplitNames.map((names) => names.length)).toEqual([14, 4, 6]);
  csvSplitNames.forEach((names, index) => assertClassCounts(names, classCounts[index] as [number, number]));
  expect(csvSplitNames.flat().sort()).toEqual(expectedFilenames);
});

test.afterAll(async () => {
  await stopLocalService();
  for (const root of temporaryRoots) await rm(root, { recursive: true, force: true });
  if (serviceLogs) process.stderr.write(serviceLogs);
});
