# Annotation Workspace Architecture

## Product intent

ANnoTation is a standalone local application for preparing, annotating, reviewing,
and exporting image datasets. Its user workflow comes from the existing Streamlit
application, while the user interface is being rebuilt as React Web. Local Python
capabilities remain behind an application API so a desktop host can start and
stop the service without embedding Streamlit.

## Runtime boundary

- `web/` contains the React/Vite application, the Canvas editor, and browser tests.
- `annotation_web/server.py` serves the built React application and a loopback-only
  JSON API. A random session token protects API calls; browser origins are checked.
- `annotation_web/service.py` owns the local application boundary. It calls the
  existing Streamlit-free process modules, reads and writes the manifest database,
  and keeps source image paths out of browser-facing item records.
- `annotation_runtime/` owns standalone app data paths and the manifest database
  access required by the process modules.
- `domain/integrations/` owns this app's external-task contracts, profile parsing,
  and connector registry; the standalone React service does not import connector
  internals from a NativeApp host checkout.
- `modules/module_010`, `012`, `014`, `017`, `018`, and `026` continue to provide
  ingestion, workspace, export, label governance, review, and local-folder scan
  logic. Their UI widgets are not used by the React application.
- `domain/tools/` provides the supported external labeling-tool adapters. A local
  YOLO model can be invoked through the same API boundary.

The React runtime supports local folder datasets, remote Service downloads, and
iWISC task listing and claim through the app-owned connector layer. For a claimed
iWISC task, React annotation sidecars are saved into the local task record before
completion. A connection may opt into a JSON result endpoint using a relative path
and POST, PUT, or PATCH. The request includes the external task ID, local task ID,
annotation JSON, classification, and operator ID. Without an endpoint, delivery
remains `unsupported`; non-success HTTP responses are recorded as delivery errors
without echoing response bodies. Synthetic HTTP tests exercise the configured
contract. Compatibility with the target service and its delivery contract remain
open. The app does not require a cloud service for local work.

## User workflow

1. Restore or create a local image dataset.
2. Annotate images in the React Canvas, apply image-level classifications, or open
   a supported external labeling tool.
3. Review images and record approval or return status.
4. Confirm the review check, choose export formats and a local output directory,
   then run export validation and generation.

The app is usable in a browser during development. The Tauri development host
loads the same React source, starts the local Python API on a dynamic loopback
port, passes that API base to React, and stops the service when the desktop
process exits. Tauri's native dialogs supply paths for local image folders,
export folders, YOLO model files, and optional external tools. Browser mode keeps
its Vite proxy and direct path fields.

The Windows NSIS bundle now includes the locked x64 Python service package under
`annotation-service/`. Release-mode Tauri starts that resource executable and
errors clearly if it is missing; only debug mode falls back to Python in the
source checkout. The service bundle smoke covers React delivery, local dataset
ingestion, annotation save, and new-process readback; it also verifies that the
service exits after its parent process terminates. A current-user NSIS candidate
was installed in a temporary directory, its host launched the bundled service
executable, and force-terminating the host caused that service to exit. Native
picker interaction, a complete packaged desktop data journey, graceful window
close interaction, and native ARM64 remain open. These candidate builds are not
release approval.

## Migration acceptance

`react-migration-test-cases.md` is the authoritative Streamlit-to-React behavior
matrix. It distinguishes implemented behavior from tested behavior and records
unsupported integration paths. The former Streamlit app and its scorecard are reference
evidence only; they do not establish React parity.

The migration is not release-complete while any required parity row is pending or
partial. The synthetic browser path now covers dataset creation, Canvas editing,
classification, review, COCO export, and service restart/readback through the real
local API. Remote Service ingestion has a local HTTP integration test. The iWISC
browser journey covers claim, annotation-sidecar persistence, configured JSON
result delivery, local completion, and read-only state through the real local API
and a synthetic external HTTP service. An unconfigured REST connection remains
unsupported. Target-Service delivery and packaged Windows architecture validation
remain open. Native picker behavior
is implemented in the development host; packaged host lifecycle remains
unverified.
