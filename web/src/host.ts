const configuredApiBase = (import.meta.env.VITE_API_BASE as string | undefined)?.replace(/\/$/, "");
let desktopApiBase: Promise<string> | undefined;

export function isDesktopHost(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

export function isNativeAppHost(): boolean {
  if (typeof window === "undefined") return false;
  const hostWindow = window as Window & {
    NATIVEAPP_LOCAL_API?: string;
    cimHost?: { chooseFile?: unknown };
  };
  return hostWindow.NATIVEAPP_LOCAL_API === "" && typeof hostWindow.cimHost?.chooseFile === "function";
}

export function supportsNativeFolderPicker(): boolean {
  return isDesktopHost() || isNativeAppHost();
}

export async function getApiBase(): Promise<string> {
  if (isNativeAppHost()) return "";
  if (configuredApiBase) return configuredApiBase;
  if (!isDesktopHost()) return "";

  desktopApiBase ??= import("@tauri-apps/api/core")
    .then(({ invoke }) => invoke<string>("get_api_base"))
    .catch((error: unknown) => {
      desktopApiBase = undefined;
      throw error;
    });
  return desktopApiBase;
}

export async function pickDirectory(defaultPath?: string): Promise<string | null> {
  if (isNativeAppHost()) {
    const hostWindow = window as unknown as Window & {
      cimHost: { chooseFile(options: { properties: string[] }): Promise<{ canceled?: boolean; paths?: string[]; path?: string }> };
    };
    const result = await hostWindow.cimHost.chooseFile({ properties: ["openDirectory"] });
    if (result.canceled) return null;
    return result.paths?.[0] ?? result.path ?? null;
  }
  if (!isDesktopHost()) return null;
  const { open } = await import("@tauri-apps/plugin-dialog");
  const value = await open({ directory: true, multiple: false, defaultPath });
  return typeof value === "string" ? value : null;
}

export async function pickModelFile(defaultPath?: string): Promise<string | null> {
  if (!isDesktopHost()) return null;
  const { open } = await import("@tauri-apps/plugin-dialog");
  const value = await open({
    directory: false,
    multiple: false,
    defaultPath,
    filters: [{ name: "YOLO models", extensions: ["pt", "onnx", "engine"] }],
  });
  return typeof value === "string" ? value : null;
}

export async function pickExecutable(defaultPath?: string): Promise<string | null> {
  if (!isDesktopHost()) return null;
  const { open } = await import("@tauri-apps/plugin-dialog");
  const value = await open({
    directory: false,
    multiple: false,
    defaultPath,
    filters: [{ name: "Applications", extensions: ["exe"] }],
  });
  return typeof value === "string" ? value : null;
}
