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
let servicePort = 0;
let temporaryRoot = "";
let serviceLogs = "";

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

test("uses a real local folder and Python service, then reads saved annotations after restart", async ({ page }) => {
  test.setTimeout(60_000);
  temporaryRoot = await mkdtemp(join(tmpdir(), "annotation-local-service-e2e-"));
  appDataDir = join(temporaryRoot, "app data");
  sourceDir = join(temporaryRoot, "資料集 with spaces");
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
  const initialAnnotation = JSON.parse(await readFile(sidecarPath, "utf8")) as { shapes: Array<{ points: number[][] }> };
  const initialPoints = initialAnnotation.shapes[0].points;

  const selectTool = page.getByRole("group", { name: "標注工具" }).getByRole("button", { name: /選取/ });
  await selectTool.click();
  const canvasMetrics = await canvas.evaluate((element: HTMLCanvasElement) => {
    const rect = element.getBoundingClientRect();
    return { x: rect.x, y: rect.y, width: rect.width, height: rect.height, bitmapWidth: element.width, bitmapHeight: element.height };
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

  await stopLocalService();
  serviceProcess = await startLocalService();
  await page.reload();
  await expect(page.getByRole("heading", { name: "標注工作台" })).toBeVisible();
  await expect(page.getByRole("button", { name: /panel-a\.png/ })).toBeVisible();
  await page.getByRole("button", { name: /panel-a\.png/ }).click();
  await expect(page.getByLabel("第 1 個框選類別")).toBeVisible();
  await expect(page.getByText("已同步")).toBeVisible();
  expect(createHash("sha256").update(await readFile(join(sourceDir, "panel-a.png"))).digest("hex")).toBe(originalImageHash);
});

test.afterAll(async () => {
  await stopLocalService();
  if (temporaryRoot) await rm(temporaryRoot, { recursive: true, force: true });
  if (serviceLogs) process.stderr.write(serviceLogs);
});
