import { expect, test } from "@playwright/test";
import { spawn, type ChildProcess } from "node:child_process";
import { once } from "node:events";
import { mkdtemp, readdir, readFile, rm } from "node:fs/promises";
import { createServer, type Server } from "node:http";
import { createServer as createNetServer, type AddressInfo } from "node:net";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const E2E_DIR = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(E2E_DIR, "../..");
const WEB_DIST = resolve(REPO_ROOT, "web/dist");
const REMOTE_ARCHIVE = Buffer.from(
  "UEsDBBQAAAAIAFMLYl3u178LZgAAAJsAAAASAAAAaW1hZ2VzL3BhbmVsLWEucG5n6wzwc+flkuJiYGDg9fRwCWJgYHBgYGAw4GBiYGDQ03ztwcDAkOHp4hhSMefqOUdBBgWeAxuCvmY4ci8S2Mh+XePEredBaoVC6hSg5+8PXGFmNWSKK/105R4DAwODp6ufyzqnhCYAUEsBAhQAFAAAAAgAUwtiXe7XvwtmAAAAmwAAABIAAAAAAAAAAAAAAIABAAAAAGltYWdlcy9wYW5lbC1hLnBuZ1BLBQYAAAAAAQABAEAAAACWAAAAAAA=",
  "base64",
);
const UPDATED_REMOTE_ARCHIVE = Buffer.from(
  "UEsDBBQAAAAIALUeZF3u178LZgAAAJsAAAASAAAAaW1hZ2VzL3BhbmVsLWIucG5n6wzwc+flkuJiYGDg9fRwCWJgYHBgYGAw4GBiYGDQ03ztwcDAkOHp4hhSMefqOUdBBgWeAxuCvmY4ci8S2Mh+XePEredBaoVC6hSg5+8PXGFmNWSKK/105R4DAwODp6ufyzqnhCYAUEsBAhQAFAAAAAgAtR5kXe7XvwtmAAAAmwAAABIAAAAAAAAAAAAAAIABAAAAAGltYWdlcy9wYW5lbC1iLnBuZ1BLBQYAAAAAAQABAEAAAACWAAAAAAA=",
  "base64",
);

let appDataDir = "";
let temporaryRoot = "";
let servicePort = 0;
let remotePort = 0;
let serviceProcess: ChildProcess | undefined;
let remoteServer: Server | undefined;
let serviceLogs = "";
const remoteRequests: string[] = [];

async function freeLoopbackPort(): Promise<number> {
  const probe = createNetServer();
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

async function startRemoteService(): Promise<void> {
  remoteServer = createServer((request, response) => {
    const path = new URL(request.url ?? "/", "http://127.0.0.1").pathname;
    remoteRequests.push(`${request.method} ${path}`);
    if (request.method === "GET" && path === "/datasets") {
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end(JSON.stringify([{ dataset_id: "remote-1", name: "Remote inspection", item_count: 1 }]));
      return;
    }
    if (request.method === "GET" && path === "/datasets/remote-1/download") {
      const downloadCount = remoteRequests.filter((request) => request === "GET /datasets/remote-1/download").length;
      const archive = downloadCount === 1 ? REMOTE_ARCHIVE : UPDATED_REMOTE_ARCHIVE;
      response.writeHead(200, { "Content-Type": "application/zip", "Content-Length": String(archive.length) });
      response.end(archive);
      return;
    }
    response.writeHead(404, { "Content-Type": "application/json" });
    response.end("{}");
  });
  await new Promise<void>((resolveListen, reject) => {
    remoteServer?.once("error", reject);
    remoteServer?.listen(0, "127.0.0.1", resolveListen);
  });
  remotePort = (remoteServer.address() as AddressInfo).port;
}

test("loads a remote Service dataset into the local annotation workspace", async ({ page }) => {
  test.setTimeout(60_000);
  temporaryRoot = await mkdtemp(join(tmpdir(), "annotation-remote-service-e2e-"));
  appDataDir = join(temporaryRoot, "app data");
  servicePort = await freeLoopbackPort();
  await startRemoteService();
  serviceProcess = await startLocalService();
  const origin = `http://127.0.0.1:${servicePort}`;

  await page.goto(origin);
  await page.getByRole("navigation", { name: "工作流程" }).getByRole("button", { name: /資料集/ }).click();
  await page.getByLabel("來源類型").selectOption("remote");
  await page.getByLabel("Service Base URL").fill(`http://127.0.0.1:${remotePort}`);
  await page.getByRole("button", { name: "載入資料集清單" }).click();
  await expect(page.getByLabel("遠端資料集")).toContainText("Remote inspection（1 張）");
  await page.getByRole("button", { name: /下載資料集並開始標注/ }).click();

  await expect(page.getByRole("heading", { name: "標注工作台" })).toBeVisible();
  await expect(page.getByRole("button", { name: /panel-a\.png/ })).toBeVisible();
  const canvas = page.getByLabel("圖片與標注框");
  await expect(canvas).toBeVisible();
  const bounds = await canvas.boundingBox();
  if (!bounds) throw new Error("The downloaded remote image canvas is not visible");
  await page.mouse.move(bounds.x + bounds.width * 0.2, bounds.y + bounds.height * 0.2);
  await page.mouse.down();
  await page.mouse.move(bounds.x + bounds.width * 0.75, bounds.y + bounds.height * 0.75);
  await page.mouse.up();
  await expect(page.getByLabel("第 1 個框選類別")).toBeVisible();
  await page.getByRole("button", { name: /保存標注/ }).click();
  await expect(page.getByText("已同步")).toBeVisible();

  const downloadEntries = await readdir(join(appDataDir, "downloads"), { withFileTypes: true });
  const downloadedDataset = downloadEntries.find((entry) => entry.isDirectory() && entry.name.startsWith("Remote_inspection_"));
  expect(downloadedDataset).toBeDefined();
  if (!downloadedDataset) throw new Error("The remote Service dataset was not kept in local app data");
  const sidecarPath = join(appDataDir, "downloads", downloadedDataset.name, "panel-a.json");
  const savedBeforeRestart = JSON.parse(await readFile(sidecarPath, "utf8")) as { shapes: Array<{ shape_type: string }> };
  expect(savedBeforeRestart.shapes).toHaveLength(1);
  expect(savedBeforeRestart.shapes[0].shape_type).toBe("rectangle");
  expect(remoteRequests).toEqual(["GET /datasets", "GET /datasets/remote-1/download"]);

  await stopLocalService();
  serviceProcess = await startLocalService();
  await page.reload();
  await page.getByRole("button", { name: /panel-a\.png/ }).click();
  await expect(page.getByLabel("第 1 個框選類別")).toBeVisible();
  const savedAfterRestart = JSON.parse(await readFile(sidecarPath, "utf8")) as { shapes: Array<{ shape_type: string }> };
  expect(savedAfterRestart.shapes).toEqual(savedBeforeRestart.shapes);

  // Legacy module 019 reuses the latest local download by default. The same
  // path must keep its React sidecar instead of silently fetching a fresh ZIP.
  await page.getByRole("navigation", { name: "工作流程" }).getByRole("button", { name: "資料集" }).click();
  await page.getByLabel("來源類型").selectOption("remote");
  await page.getByLabel("Service Base URL").fill(`http://127.0.0.1:${remotePort}`);
  await page.getByRole("button", { name: "載入資料集清單" }).click();
  await page.getByRole("button", { name: /下載資料集並開始標注/ }).click();
  await expect(page.getByRole("button", { name: /panel-a\.png/ })).toBeVisible();
  await expect(page.getByLabel("第 1 個框選類別")).toBeVisible();
  expect(remoteRequests.filter((request) => request === "GET /datasets/remote-1/download")).toHaveLength(1);

  // Explicit overwrite re-fetches the remote dataset and opens its new content.
  await page.getByRole("navigation", { name: "工作流程" }).getByRole("button", { name: "資料集" }).click();
  await page.getByRole("checkbox", { name: "重新下載（覆蓋現有）" }).check();
  await page.getByRole("button", { name: /下載資料集並開始標注/ }).click();
  await expect(page.getByRole("button", { name: /panel-b\.png/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /panel-a\.png/ })).toHaveCount(0);
  expect(remoteRequests.filter((request) => request === "GET /datasets/remote-1/download")).toHaveLength(2);
});

test.afterAll(async () => {
  await stopLocalService();
  if (remoteServer) {
    await new Promise<void>((resolveClose, reject) => {
      remoteServer?.close((error) => error ? reject(error) : resolveClose());
    });
  }
  if (temporaryRoot) await rm(temporaryRoot, { recursive: true, force: true });
  if (serviceLogs) process.stderr.write(serviceLogs);
});
