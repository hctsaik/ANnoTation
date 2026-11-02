# ANnoTation React Web

ANnoTation is being rebuilt as a local React application with a Python service
for dataset scanning, annotations, review, exports, external tools, and local
model inference. The React app does not launch Streamlit. Legacy Streamlit pages
remain in the source tree as migration references and are not part of this
runtime.

**Migration status:** local-folder and remote Service ingestion, synthetic HTTP
iWISC task listing and claim, the rectangle Canvas workflow, classification,
review, a COCO export path, and a confirmed label-merge impact preview are
implemented. The iWISC path saves React sidecars into the local task record and
supports local task completion. A connection can opt into JSON result delivery by
configuring its relative API path and method; without a path, external delivery
remains unsupported. The local workflow has a synthetic real-service browser
journey, and iWISC has a separate browser-to-service journey against a local HTTP
fixture. This branch is not release-complete:
see the
[migration acceptance matrix](docs/react-migration-test-cases.md) for pending
source modes, packaged desktop integration, and remaining end-to-end coverage.

## Run the built local app

From PowerShell at the repository root:

```powershell
py -m pip install -r requirements-web.txt
Set-Location web
npm ci
npm run build
Set-Location ..
py -m annotation_web.server --host 127.0.0.1 --port 4173
```

Open the local address printed by the server. The service binds only to loopback
and uses a per-process session token for its API. Set `APP_DATA_DIR` before
launching to choose the local database and settings directory; by default they
are stored in `~/.annotation-standalone`.

## Develop the React UI

Use a Node release supported by Vitest 5: Node 22.12.x, 24.x, or 26+.

Run the Python service on port 4173 in one terminal, then start Vite in another:

```powershell
Set-Location web
npm run dev
```

Vite proxies API requests to the local Python service. Browser mode accepts paths
as text. The Tauri development host exposes native folder and file pickers and
starts the loopback Python service. In a NativeApp public web host, React uses
same-origin `/api` requests and `cimHost.chooseFile` for local dataset and export
folders. A browser file input uploads YOLO models through that same-origin API;
native model and external-tool executable pickers remain Tauri-only. Browser
journeys cover local dataset creation through the injected NativeApp folder
contract and model upload through the local service, but do not verify a
packaged NativeApp host:

```powershell
py -3.11-64 -m pip install -r requirements-web.txt
Set-Location web
npm ci
npm run tauri:dev
```

Set `ANNOTATION_PYTHON` to a Python 3.11 executable if the Python launcher does
not select the intended environment or architecture. The development host stores
its service log and app data in the operating system's per-user app-data
directory. The Windows NSIS candidate bundles a separate Python service runtime;
the release host starts that bundled executable and does not fall back to a
machine-installed Python. The candidate has been built and its installed host
was observed launching the bundled service. This does not establish full desktop
UI parity, native ARM64 support, model-runtime support, or release readiness.

## Build the Windows Python service bundle

The bundle profile contains CPython 3.11 x64, the locked service dependencies,
and the built React assets. YOLO/PyTorch/OpenCV are optional and are excluded from
this base profile. From a clean checkout on Windows:

```powershell
py -3.11-64 -m venv .venv-web-package
& .\.venv-web-package\Scripts\python.exe -m pip install -r requirements-web-package.txt
npm --prefix web ci
& .\.venv-web-package\Scripts\python.exe scripts\build_annotation_web_bundle.py
& .\.venv-web-package\Scripts\python.exe scripts\smoke_annotation_web_bundle.py
npm --prefix web run tauri -- build --bundles nsis
```

The build script rejects a Python environment whose installed versions differ
from the lock. The smoke test launches the frozen service, loads its React page,
creates a local dataset, saves an annotation, then stops and restarts the service
and verifies the dataset and annotation read back. It also starts the service
with a parent PID, terminates that parent, and verifies the service exits. Output
is written to
`dist/annotation-web/`; it is a local build artifact and is not committed. This
profile has only been built and exercised as CPython 3.11 AMD64 on Windows ARM64
emulation. The NSIS candidate uses current-user install mode and was 16.37 MiB
in the observed build. It launched its bundled service after installation in a
temporary directory; force-terminating the installed host also caused that
service process to exit. Screen interaction, native picker use, native ARM64,
and the optional model runtime remain unverified.

## Local model support

The React workbench can import a `.pt`, `.onnx`, or `.engine` YOLO model up to
256 MiB through the authenticated loopback API. The Python service stores it in
the app-owned data directory and returns a content-addressed reference without
exposing its filesystem path to React. Tauri can still use its native model
picker and an existing local path. Install `requirements-web-ai.txt` in a Python
environment that has compatible PyTorch wheels before running YOLO inference.
Real model/runtime compatibility has not yet been validated on every target
architecture.

## Verification

```powershell
py -m pip install -r requirements-web-test.txt
Set-Location web
npm test
npm run build
npx playwright install chromium
npm run e2e
Set-Location ..
py -m pytest tests/test_annotation_web_service.py tests/annotation/test_configurable_rest_delivery.py -q
```

The browser E2E suite includes a real local-folder → React → Python service
journey. It uses a temporary nested image folder, creates and edits a rectangle,
preserves existing polygon sidecars during a React rectangle edit, applies and
clears image classification across image navigation, saves review
data including approval and rejection with comments, saves and restores the
configured external tool, and checks COCO, YOLO, Pascal VOC, ImageFolder, and CSV
exports with a 100/0/0 split. After restarting the service, it reads the
annotation, cleared classification, external tool, review decisions and
comments, and all five export-history records back. It also confirms an invalid
Windows output path is reported as a failed attempt without replacing that
history. The API is not mocked for this case, and the source image hash is
checked for changes. A second browser journey registers a synthetic iWISC
HTTP service, lists and claims a task, downloads its ZIP, opens the image, saves
annotation sidecars, delivers the configured JSON result, and verifies the
read-only editor. Python tests also verify the unconfigured unsupported state.
Build `web/dist` before running the
suite. On Windows the tests start the
service with `py -3.14`; set `ANNOTATION_PYTHON` to another executable when needed.

## Architecture and project context

- [Workspace architecture](docs/annotation-workspace-architecture.md)
- [Streamlit-to-React acceptance cases](docs/react-migration-test-cases.md)
- [Historical Streamlit E2E scorecard](docs/annotation-app-e2e-scorecard.md)
- [External tool packaging](external-tools/README.md)
- [Plugin asset manifest](plugin.manifest.yaml)

The existing process modules are reused behind the local service boundary, and
the connector contracts are owned by this repository so it can run independently
of a NativeApp checkout. The React runtime supports local folders, remote Service
dataset downloads, and synthetic iWISC listing and claim. Verification against a
target iWISC service and a packaged native desktop host remain pending.
