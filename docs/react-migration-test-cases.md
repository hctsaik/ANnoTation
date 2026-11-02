# Streamlit-to-React Migration Test Cases

This is the acceptance matrix for the React rewrite. The existing Streamlit
application and its [E2E scorecard](annotation-app-e2e-scorecard.md) describe the
behavior being migrated; their results do not count as React verification.

## Current status

The React branch has a working local-folder workflow and a first Canvas editor.
“Implemented” means a user path exists in the code. It does not mean the full
legacy scenario has passed end-to-end. Release parity remains open while a row is
partial or pending.

Latest full local checks (2026-11-02): 26 Vitest tests, 10 Playwright tests,
24 Python tests across the web service and configurable result connector, and
the production Vite build pass. The broader 183 Python tests under `tests/`
also passed in the preceding validation run. Windows GitHub Actions passed the
then-current service, React, build, and 10-browser-test gates for commit
`0d38efc` in [hosted run 41395589408](https://github.com/hctsaik/ANnoTation/actions/runs/41395589408).
The later run for docs-only commit `2ab7a8a` exposed a review-note race while an
image's detail was loading; the UI now keeps review controls disabled until the
selected image detail is ready, with unit and browser regression coverage. The
run for `e0b8707` then exposed the same stale-detail race in image classification:
the selection could revert after a clear. Classification controls now wait for
the selected detail and the save handler checks that detail before writing. The
26-test Vitest suite, production build, and 10-test Playwright suite pass locally;
hosted confirmation for this fix is pending.
Remote Service downloads use runtime `requests`; iWISC and configurable REST
connectors use runtime `httpx`. Tests add `respx` from
`requirements-web-test.txt`.
Full pytest collection currently stops because two legacy module tests expect
`C:\code\claude\scripts\shared\_manifest_db.py` outside this checkout.
The locked CPython 3.11 AMD64 service bundle built in a fresh venv and passed its
frozen smoke journey: React static page, health/session API, local dataset import,
annotation save, and readback after a new service process. Optional YOLO/PyTorch,
OpenCV, NumPy, cryptography, and psutil modules were absent from that bundle.
The Windows NSIS installer also built (16.37 MiB) and, after current-user
installation to a temporary directory, launched the bundled service executable
with its packaged React asset directory. The installed service received the
Tauri host PID and exited after that host was force-terminated; the frozen
service smoke also verifies this parent-exit behavior. UI interaction was not
performed.
The Tauri dev host launched on this Windows ARM64 machine as an x86_64 build; its
loopback service returned a session token on a dynamic port and exited with the
host. The installed NSIS host now has process-level evidence for bundled-service
launch and cleanup after abrupt host termination. Native picker UI inspection,
a complete packaged desktop journey, graceful window-close interaction, native
ARM64, target-Service, and real-model checks remain pending.

| ID | Existing behavior to preserve | React implementation | Evidence and remaining work | Status |
|---|---|---|---|---|
| APP-01 | Restore an existing dataset and keep the four workflow stages available | Last-used manifest is prioritized; four workflow stages remain available | React unit test and Playwright workflow case pass locally | Implemented; CI run pending |
| DATA-01 | Create a dataset from a local folder, scan recursively, and accept supported image extensions | Local folder source calls module 026 and stores a manifest | `web/e2e/local-service.spec.ts` creates a dataset through the built React app and Python service, visibly opens JPG/JPEG/PNG/BMP/WEBP/TIF/TIFF and uppercase PNG at 16×12, excludes GIF/text files, compares source hashes, and verifies recursive vs root-only counts; `tests/test_annotation_web_service.py::test_local_dataset_scans_supported_formats_recursively_or_root_only` covers the same service matrix | Implemented; local browser-to-service format matrix and service matrix pass; CI and packaged-host evidence pending |
| HANDOFF-01 | Receive an LV content-addressed handoff, show its existing annotation, save edits beside the hash-named image, and preserve the image for LV readback | The React local-folder workflow imports the handoff's `images/` directory and loads/saves its adjacent LabelMe JSON sidecars | `web/e2e/local-service.spec.ts` opens a SHA-named image and seeded rectangle in React, moves and saves it through the real Python service, restarts the service, and checks the edited sidecar plus unchanged image bytes. A separate local interoperability run used LV's actual handoff writer and readback with the Annotation service | Implemented; local and hosted browser suites pass 10/10; packaged-host handoff journey remains unverified |
| DATA-02 | Ingest remote and iWISC task sources | React can list and download remote Service datasets through modules 019/026; it can register an iWISC connection, list and claim tasks, load images locally, save React sidecars into the linked task record, and complete a task locally with a read-only final state | `web/e2e/iwisc-source.spec.ts` drives the built React app through the real local API and a synthetic HTTP iWISC service, including opt-in JSON result delivery, API-key handling, claim, ZIP download, annotation save, completion, and read-only state; Python tests cover unconfigured `unsupported`, configured request fields, delivery errors, registration validation, task-record persistence, and mutation lockout; target Service validation remains | Partial; configured delivery passes against the synthetic contract, but the target Service endpoint and payload semantics remain unverified |
| SET-01 | Configure annotation labels, classification labels, and the external tool | React settings call the module 012 process layer | The real-service browser journey saves annotation/classification labels and the LabelMe tool choice, restarts the service, and verifies the tool choice reads back | Implemented; local settings persistence journey passes; CI and packaged-host evidence pending |
| LIST-01 | Search names, filter annotation/classification/review state, clear filters, and paginate | React list supports search, status, classification, low-confidence, label filters, and 20-item pages | Model/App tests and Playwright filter case pass; App tests cover the 20/21-item boundary, disabled page controls, combined search/status/classification/confidence filters, review-status plus annotation-label filters, and returning to page one or clearing review filters | Implemented; local UI coverage complete; CI and packaged-host evidence pending |
| WORK-01 | Draw, move, resize, delete, undo, and redo rectangle annotations; navigate between images | React Canvas supports drag drawing, moving, lower-right resizing, deletion, undo/redo, and image navigation | `web/e2e/local-service.spec.ts` exercises draw, move, resize, delete, undo, and redo through the built React app, saves through the real API, switches images, restarts the service, and verifies the sidecar-backed rectangle is read back; representative user-file fixture and CI evidence remain | Implemented; synthetic real-service editor journey passes locally; broader fixture and CI evidence pending |
| WORK-02 | Preserve existing polygons and other non-rectangle X-AnyLabeling shapes during rectangle edits | React conversion keeps non-rectangle shapes and their relative order | Model and Python service round-trip tests cover polygon preservation; `web/e2e/local-service.spec.ts` opens a real sidecar with two polygons, draws and saves a React rectangle, then verifies both polygon geometries and order remain unchanged | Implemented; local real-service polygon preservation journey passes; CI and representative user-file fixture pending |
| WORK-03 | Apply and clear image-level classification with keyboard/quick actions | React classification control saves changes immediately | The real-service browser journey applies a classification, clears it, switches to another image and back, then restarts the service and verifies the cleared value remains empty | Implemented; local real-service apply/clear persistence journey passes; CI and packaged-host evidence pending |
| TOOL-01 | Launch X-AnyLabeling, LabelMe, or ISAT for the selected image | React calls the local API with the current item, selected adapter, and optional executable override | App test verifies the button sends the selected image/tool/path; service HTTP test verifies the granted image reaches the adapter without exposing its path in the response; adapter test verifies LabelMe receives the image and adjacent JSON output path; installed-tool launch on supported Windows architectures remains | Implemented; UI-to-adapter contract tests pass; installed-tool runtime verification pending |
| AI-01 | Run local YOLO pre-labeling on one image or a dataset with confidence and overwrite controls | React calls the local Python model in sequential batches of at most 500 images, reports batch progress, and aggregates results; partial failure refreshes completed work and reports which batches finished | App tests verify a 501-image dataset is sent as 500 + 1 and totals are combined, and that a later batch failure reports completed work; `tests/test_annotation_web_service.py` covers skip-without-model-load, overwrite and confidence forwarding, malformed-model errors, and empty-prediction persistence (11 service tests pass); the base Windows service bundle intentionally excludes optional YOLO/PyTorch/OpenCV; authorized real-model and architecture checks remain | Implemented; deterministic service coverage passes; real model/runtime validation pending |
| LABEL-01 | Scan label counts, identify near duplicates, rename, merge, and delete labels | React label manager previews source file-operation counts, box/classification counts, and target usage before confirming a merge; it displays affected-image counts and confirms deletion | web/src/App.test.tsx covers the preview and cancel/confirm choice; web/e2e/local-service.spec.ts runs the preview, cancels once, confirms once, and verifies the real Python service rewrites persisted sidecars. Existing Python service tests cover scan/rename/merge/delete and path privacy | Implemented; local visible real-service merge journey passes; CI and packaged-host evidence pending |
| REVIEW-01 | Filter the QA queue and persist approve/reject status and comments | Review stage records per-image status and comment; review confirmation is stored in browser local storage | The real-service browser journey saves both an approval and a rejection with comments, restarts the service, filters for rejected items, and verifies the rejected status and comment in React | Implemented; local approve/reject persistence journey passes; CI and packaged-host evidence pending |
| EXPORT-01 | Select COCO, YOLO, VOC, ImageFolder, or CSV; optionally split data; block unsafe export | React export panel checks completion, partial-export opt-in, review confirmation, and module 014 validation | The real-service browser journey selects all five formats with a 100/0/0 split and checks COCO boxes, YOLO labels, VOC XML/index, ImageFolder copy, CSV split/label values, and all five history records after service restart; an invalid Windows output path reports this attempt's error while retaining the prior successful history | Implemented; local multi-format, split, and path-failure journeys pass; CI and other split-ratio evidence pending |
| LOCAL-01 | Keep local data inside the app boundary and reject unrelated image paths | Loopback API uses a session token, checks browser origin, and applies the dataset folder grant | Python service/API tests check token, origin, and path grant | Implemented; tests pass locally |
| HOST-01 | Open the app as a native desktop window with local file/folder pickers and controlled sidecar lifecycle | Tauri development host starts the source Python service; release mode launches the bundled Windows x64 service resource and exposes native directory/model/tool pickers | The locked service smoke verifies React delivery, local ingestion, annotation persistence, new-process readback, and service exit when its parent terminates. A 16.37 MiB current-user NSIS candidate built; the installed Tauri process launched `annotation-service.exe` from the installer resource directory with its PID, and the service exited after the host was force-terminated. Full packaged UI/data flow, native picker interaction, graceful worker shutdown through user window close, and native ARM64 remain unverified | Partial; packaged launch and abrupt-host cleanup are verified, end-to-end desktop parity remains open |

## Required gates before claiming parity

1. Every required row above is implemented or explicitly removed from the product
   scope with an owner decision.
2. Browser tests cover the full create → annotate → save → close/reopen → review →
   export path on a synthetic dataset using the real local API, not only a mock.
3. Service tests cover malformed inputs, local path boundaries, and persisted
   annotations, classifications, review decisions, and exports.
4. The packaged desktop host bundles or installs the supported Python runtime,
   starts the local service without opening a console, supports the intended
   Windows architectures, and passes the same workflow. The current Tauri
   development host does not satisfy this release gate.

The test layers are kept beside the code: React unit tests in web/src/, browser
tests in web/e2e/, and Python service/API tests in tests/.
