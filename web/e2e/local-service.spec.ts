import { expect, test } from "@playwright/test";
import { createHash } from "node:crypto";
import { spawn, type ChildProcess } from "node:child_process";
import { once } from "node:events";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
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
  await page.getByLabel("資料夾路徑").fill(exportDir);
  await page.getByRole("button", { name: /檢查並開始匯出/ }).click();
  await expect(page.getByRole("heading", { name: "匯出完成" })).toBeVisible();
  const cocoExport = JSON.parse(await readFile(join(exportDir, "coco_json", "all.json"), "utf8")) as {
    images: Array<{ file_name: string }>;
    annotations: Array<{ bbox: number[] }>;
  };
  expect(cocoExport.images.map((image) => image.file_name).sort()).toEqual(["panel-a.png", "panel-b.png"]);
  expect(cocoExport.annotations).toHaveLength(1);
  expect(cocoExport.annotations[0].bbox[2]).toBeGreaterThan(0);

  await stopLocalService();
  serviceProcess = await startLocalService();
  await page.reload();
  await expect(page.getByRole("heading", { name: "標注工作台" })).toBeVisible();
  await expect(page.getByRole("button", { name: /panel-a\.png/ })).toBeVisible();
  await page.getByRole("button", { name: /panel-a\.png/ }).click();
  await expect(page.getByLabel("第 1 個框選類別")).toBeVisible();
  await expect(page.getByLabel("分類結果")).toHaveValue("OK");
  const restoredPanel = page.getByRole("button", { name: /panel-a\.png/ });
  await expect(restoredPanel).toContainText("OK");
  await expect(restoredPanel).toContainText("已核准");
  await expect(page.getByText("已同步")).toBeVisible();
  await workflow.getByRole("button", { name: "審核" }).click();
  await expect(page.getByLabel("審核備註")).toHaveValue("邊界確認完成");
  await workflow.getByRole("button", { name: "匯出" }).click();
  await expect(page.getByText("coco_json", { exact: true })).toBeVisible();
  expect(createHash("sha256").update(await readFile(join(sourceDir, "panel-a.png"))).digest("hex")).toBe(originalImageHash);

  await workflow.getByRole("button", { name: "標注工作台" }).click();
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

test.afterAll(async () => {
  await stopLocalService();
  for (const root of temporaryRoots) await rm(root, { recursive: true, force: true });
  if (serviceLogs) process.stderr.write(serviceLogs);
});
