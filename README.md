# ANnoTation React Web

ANnoTation is being rebuilt as a local React application with a Python service
for dataset scanning, annotations, review, exports, external tools, and local
model inference. The React app does not launch Streamlit. Legacy Streamlit pages
remain in the source tree as migration references and are not part of this
runtime.

**Migration status:** the local-folder workflow and first Canvas editor are
implemented. This branch is not release-complete: see the
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

## Architecture and project context

- [Workspace architecture](docs/annotation-workspace-architecture.md)
- [Streamlit-to-React acceptance cases](docs/react-migration-test-cases.md)
- [Historical Streamlit E2E scorecard](docs/annotation-app-e2e-scorecard.md)
- [External tool packaging](external-tools/README.md)
- [Plugin asset manifest](plugin.manifest.yaml)

The existing process modules are reused behind the local service boundary.
The React runtime currently supports local folder datasets. Remote and iWISC
ingestion and a packaged native desktop host remain pending.
