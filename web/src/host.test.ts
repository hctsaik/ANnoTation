import { beforeEach, describe, expect, it, vi } from "vitest";
import { getApiBase, isDesktopHost, isNativeAppHost, pickDirectory, pickExecutable, pickModelFile, supportsNativeFolderPicker } from "./host";

const mocks = vi.hoisted(() => ({
  invoke: vi.fn(),
  open: vi.fn(),
}));

vi.mock("@tauri-apps/api/core", () => ({ invoke: mocks.invoke }));
vi.mock("@tauri-apps/plugin-dialog", () => ({ open: mocks.open }));

function setDesktopHost(enabled: boolean) {
  if (enabled) Object.defineProperty(window, "__TAURI_INTERNALS__", { configurable: true, value: {} });
  else Reflect.deleteProperty(window, "__TAURI_INTERNALS__");
}

function setNativeAppHost(chooseFile?: (options: { properties: string[] }) => Promise<unknown>) {
  if (!chooseFile) {
    Reflect.deleteProperty(window, "NATIVEAPP_LOCAL_API");
    Reflect.deleteProperty(window, "cimHost");
    return;
  }
  Object.defineProperty(window, "NATIVEAPP_LOCAL_API", { configurable: true, value: "" });
  Object.defineProperty(window, "cimHost", { configurable: true, value: { chooseFile } });
}

describe("desktop host bridge", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setDesktopHost(false);
    setNativeAppHost();
  });

  it("keeps the browser on the configured API base and does not invoke Tauri", async () => {
    expect(isDesktopHost()).toBe(false);
    expect(await getApiBase()).toBe(import.meta.env.VITE_API_BASE ?? "");
    expect(mocks.invoke).not.toHaveBeenCalled();
    expect(await pickDirectory()).toBeNull();
    expect(mocks.open).not.toHaveBeenCalled();
  });

  it("resolves the service URL from the desktop host and opens native pickers", async () => {
    setDesktopHost(true);
    mocks.invoke.mockResolvedValue("http://127.0.0.1:43210");
    mocks.open
      .mockResolvedValueOnce("D:\\datasets")
      .mockResolvedValueOnce("D:\\models\\yolo.pt")
      .mockResolvedValueOnce("C:\\Tools\\labelme.exe");

    expect(await getApiBase()).toBe("http://127.0.0.1:43210");
    expect(await pickDirectory("D:\\previous")).toBe("D:\\datasets");
    expect(await pickModelFile()).toBe("D:\\models\\yolo.pt");
    expect(await pickExecutable()).toBe("C:\\Tools\\labelme.exe");
    expect(mocks.invoke).toHaveBeenCalledWith("get_api_base");
    expect(mocks.open).toHaveBeenNthCalledWith(1, { directory: true, multiple: false, defaultPath: "D:\\previous" });
    expect(mocks.open).toHaveBeenNthCalledWith(2, {
      directory: false,
      multiple: false,
      defaultPath: undefined,
      filters: [{ name: "YOLO models", extensions: ["pt", "onnx", "engine"] }],
    });
    expect(mocks.open).toHaveBeenNthCalledWith(3, {
      directory: false,
      multiple: false,
      defaultPath: undefined,
      filters: [{ name: "Applications", extensions: ["exe"] }],
    });
  });

  it("uses the public same-origin API and folder picker in NativeApp", async () => {
    const chooseFile = vi.fn().mockResolvedValue({ canceled: false, paths: ["D:\\datasets\\inspection"] });
    setNativeAppHost(chooseFile);

    expect(isNativeAppHost()).toBe(true);
    expect(supportsNativeFolderPicker()).toBe(true);
    expect(await getApiBase()).toBe("");
    expect(await pickDirectory()).toBe("D:\\datasets\\inspection");
    expect(chooseFile).toHaveBeenCalledWith({ properties: ["openDirectory"] });
    expect(mocks.invoke).not.toHaveBeenCalled();
    expect(mocks.open).not.toHaveBeenCalled();
  });
});
