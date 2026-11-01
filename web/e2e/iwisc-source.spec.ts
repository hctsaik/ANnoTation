import { expect, test } from "@playwright/test";
import { spawn, type ChildProcess } from "node:child_process";
import { once } from "node:events";
import { mkdtemp, rm } from "node:fs/promises";
import { createServer, type Server } from "node:http";
import { createServer as createNetServer, type AddressInfo } from "node:net";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const E2E_DIR = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(E2E_DIR, "../..");
const WEB_DIST = resolve(REPO_ROOT, "web/dist");
const TASK_ARCHIVE = Buffer.from(
  "UEsDBBQAAAAIAFMLYl3u178LZgAAAJsAAAASAAAAaW1hZ2VzL3BhbmVsLWEucG5n6wzwc+flkuJiYGDg9fRwCWJgYHBgYGAw4GBiYGDQ03ztwcDAkOHp4hhSMefqOUdBBgWeAxuCvmY4ci8S2Mh+XePEredBaoVC6hSg5+8PXGFmNWSKK/105R4DAwODp6ufyzqnhCYAUEsBAhQAFAAAAAgAUwtiXe7XvwtmAAAAmwAAABIAAAAAAAAAAAAAAIABAAAAAGltYWdlcy9wYW5lbC1hLnBuZ1BLBQYAAAAAAQABAEAAAACWAAAAAAA=",
  "base64",
);

let appDataDir = "";
let temporaryRoot = "";
let servicePort = 0;
let serviceProcess: ChildProcess | undefined;
let externalServer: Server | undefined;
let externalPort = 0;
const externalRequests: string[] = [];
let serviceLogs = "";

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

async function startSyntheticIwisc(): Promise<void> {
  externalServer = createServer((request, response) => {
    const path = new URL(request.url ?? "/", "http://127.0.0.1").pathname;
    externalRequests.push(`${request.method} ${path}`);
    if (request.method === "GET" && path === "/getAntList") {
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end(JSON.stringify([
        { antID: "ant-e2e-01", antActive: 0, antPeriod: "2026-11", line: "L01" },
        { antID: "ant-e2e-done", antActive: 2, antPeriod: "2026-10" },
      ]));
      return;
    }
    if (request.method === "POST" && path === "/getAntTaskDetail") {
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ download_url: `http://127.0.0.1:${externalPort}/artifact.zip` }));
      return;
    }
    if (request.method === "PATCH" && path === "/tasks/ant-e2e-01/claim") {
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end("{}");
      return;
    }
    if (request.method === "GET" && path === "/artifact.zip") {
      response.writeHead(200, { "Content-Type": "application/zip" });
      response.end(TASK_ARCHIVE);
      return;
    }
    response.writeHead(404, { "Content-Type": "application/json" });
    response.end("{}");
  });
  await new Promise<void>((resolveListen, reject) => {
    externalServer?.once("error", reject);
    externalServer?.listen(0, "127.0.0.1", resolveListen);
  });
  externalPort = (externalServer.address() as AddressInfo).port;
}

test("registers an iWISC service, lists tasks, claims one, and opens its local image", async ({ page }) => {
  test.setTimeout(60_000);
  temporaryRoot = await mkdtemp(join(tmpdir(), "annotation-iwisc-e2e-"));
  appDataDir = join(temporaryRoot, "app-data");
  servicePort = await freeLoopbackPort();
  await startSyntheticIwisc();
  serviceProcess = await startLocalService();

  await page.goto(`http://127.0.0.1:${servicePort}`);
  await page.getByRole("navigation", { name: "工作流程" }).getByRole("button", { name: /資料集/ }).click();
  await page.getByLabel("來源類型").selectOption("iwisc");

  await page.getByText("新增外部系統連線").click();
  await page.getByLabel("連線別名").fill("Synthetic AOI");
  await page.getByLabel("iWISC Service 位址").fill(`http://127.0.0.1:${externalPort}`);
  await page.getByLabel("iWISC API 金鑰").fill("synthetic-secret");
  await page.getByRole("button", { name: "新增連線" }).click();
  await expect(page.getByText("已新增外部系統連線：Synthetic AOI")).toBeVisible();
  await expect(page.getByLabel("iWISC API 金鑰")).toHaveValue("");
  await expect(page.locator("body")).not.toContainText("synthetic-secret");

  await page.getByLabel("使用者 ID").fill("worker-e2e");
  await page.getByRole("button", { name: "查看任務清單" }).click();
  await expect(page.getByTestId("iwisc-task-ant-e2e-01")).toBeVisible();
  await expect(page.getByTestId("iwisc-task-ant-e2e-done")).toHaveCount(0);
  await page.getByTestId("iwisc-task-ant-e2e-01").click();
  await page.getByRole("button", { name: /認領任務並載入影像/ }).click();

  await expect(page.getByRole("heading", { name: "標注工作台" })).toBeVisible();
  await expect(page.getByRole("button", { name: /panel-a\.png/ })).toBeVisible();
  expect(externalRequests).toContain("GET /getAntList");
  expect(externalRequests).toContain("POST /getAntTaskDetail");
  expect(externalRequests).toContain("PATCH /tasks/ant-e2e-01/claim");
  expect(externalRequests).toContain("GET /artifact.zip");
});

test.afterAll(async () => {
  await stopLocalService();
  if (externalServer) {
    await new Promise<void>((resolveClose, reject) => {
      externalServer?.close((error) => error ? reject(error) : resolveClose());
    });
  }
  if (temporaryRoot) await rm(temporaryRoot, { recursive: true, force: true });
  if (serviceLogs) process.stderr.write(serviceLogs);
});
