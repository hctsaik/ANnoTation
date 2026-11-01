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
iWISC task listing and claim through the app-owned connector layer. The synthetic
iWISC HTTP integration is covered locally; compatibility with the target service
remains open. The app does not require a cloud service for local work.

## User workflow

1. Restore or create a local image dataset.
2. Annotate images in the React Canvas, apply image-level classifications, or open
   a supported external labeling tool.
3. Review images and record approval or return status.
4. Confirm the review check, choose export formats and a local output directory,
   then run export validation and generation.

The app is usable in a browser during development. A future desktop host can bundle
the same React build and launch the local API as a sidecar; a native file-picker
bridge is not part of this branch yet.

## Migration acceptance

`react-migration-test-cases.md` is the authoritative Streamlit-to-React behavior
matrix. It distinguishes implemented behavior from tested behavior and records
unsupported legacy paths. The former Streamlit app and its scorecard are reference
evidence only; they do not establish React parity.

The migration is not release-complete while any required parity row is pending or
partial. The synthetic browser path now covers dataset creation, Canvas editing,
classification, review, COCO export, and service restart/readback through the real
local API. Remote Service and synthetic iWISC ingestion have local HTTP integration
tests; compatibility with the target Service, a native file-picker bridge, and
packaged Windows architecture validation remain open.
