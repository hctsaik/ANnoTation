# ANnoTation React Web

ANnoTation is being rebuilt as a local React application with a Python service
for dataset scanning, annotations, review, exports, external tools, and local
model inference. The React app does not launch Streamlit. Legacy Streamlit pages
remain in the source tree as migration references and are not part of this
runtime.

**Migration status:** local-folder and remote Service ingestion, synthetic HTTP
iWISC task listing and claim, the rectangle Canvas workflow, classification,
review, a COCO export path, and a confirmed label-merge impact preview are
implemented. The iWISC path also saves React sidecars into the local task record and
supports local task completion; the current
REST connector reports external result delivery as unsupported. The local workflow
has a synthetic real-service browser journey, and iWISC has a separate browser-to-
service journey against a local HTTP fixture. This branch is not release-complete:
see the
[migration acceptance matrix](docs/react-migration-test-cases.md) for pending
source modes, desktop integration, and end-to-end coverage.

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

Vite proxies API requests to the local Python service. Dataset and model paths
are entered directly for now; native file and folder pickers require the desktop
host bridge tracked in the acceptance matrix.

## Local model support

The API can run YOLO pre-labeling with a local model file. Install
`requirements-web-ai.txt` in a Python environment that has compatible PyTorch
wheels. Model runtime compatibility has not yet been validated on every target
architecture.

## Verification

```powershell
Set-Location web
npm test
npm run build
npx playwright install chromium
npm run e2e
Set-Location ..
py -m pip install pytest
py -m pytest tests/test_annotation_web_service.py -q
```

The browser E2E suite includes a real local-folder → React → Python service
journey. It uses a temporary nested image folder, creates and edits a rectangle,
saves classification and review data, generates and checks a COCO export, then
restarts the service and reads the annotation, classification, review comment,
and export history back. The API is not mocked for this case, and the source image
hash is checked for changes. A second browser journey registers a synthetic iWISC
HTTP service, lists and claims a task, downloads its ZIP, opens the image, saves
annotation sidecars, completes the local task, and verifies the unsupported
external-delivery state and read-only editor. Build `web/dist` before running the
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
