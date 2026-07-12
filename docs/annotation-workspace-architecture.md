# Annotation Workspace Architecture

## Product intent

Annotation is a standalone work application based on the business flow formerly
declared by `sheet-annotation_workflow`. Operators should not need
to understand the platform's `input / process / output` implementation layers.
Opening the application restores the most recent dataset and enters one
continuous workspace.

## User workflow

1. Open Annotation App.
2. If no dataset exists, show a focused onboarding state that points to Data
   Source.
3. Otherwise restore the last dataset and initialise the annotation session
   automatically.
4. Work from one screen: progress, pending work, filters, image, annotation
   tools, AI assistance, sync status, and export.
5. Persist global settings immediately. Background refresh and annotation-file
   discovery must not require a separate Execute step.

## Architecture

- `012_runner.py` owns the Annotation App shell, application composition, and
  session state.
- The app shell exposes product stages (`總覽`, `資料來源`, `標注工作台`,
  `標籤管理`, `審核`, `匯出`) rather than platform module tabs. AI assistance
  and sync are contextual capabilities inside the workspace instead of separate
  destinations.
- Existing module 026/012/015/017/018/014 input, process, and output implementations are
  composed inside the shell during migration; their layering is not exposed to
  users. Legacy deployments without module 026 fall back to module 010 for
  local data ingestion.
- `012_process.py` remains a Streamlit-free application/service layer.
- `012_output.py` owns the annotation workspace components.
- `_config.py` and the manifest database remain the sources of truth.
- The host receives `mode=single-page` and mounts one application iframe.
- Tauri may implement the same start/stop contract without recreating
  Input/Output tabs.

The legacy `012_input.py` remains available for compatibility with old sheet
definitions during migration, but it is not the primary Annotation Session UI.

## Acceptance criteria

- Starting `module_012` shows no Input/Output or platform Sheet navigation.
- Data source, annotation, review, and export are reachable inside one app.
- An existing dataset opens without pressing Execute.
- Dataset, labels, annotation tool, classification labels, and refresh settings
  are available from a collapsible settings sidebar.
- Changing a setting rebuilds the session without losing the selected dataset.
- Pending work, annotation launch, AI settings, sync, and export remain
  accessible from the same workspace.
- Empty-dataset, service-error, and restart states are visible and recoverable.
- The host contract is shell-neutral and suitable for Portal or Tauri.
