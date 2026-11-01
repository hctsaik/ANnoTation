# Streamlit-to-React Migration Test Cases

This is the acceptance matrix for the React rewrite. The existing Streamlit
application and its [E2E scorecard](annotation-app-e2e-scorecard.md) describe the
behavior being migrated; their results do not count as React verification.

## Current status

The React branch has a working local-folder workflow and a first Canvas editor.
“Implemented” means a user path exists in the code. It does not mean the full
legacy scenario has passed end-to-end. Release parity remains open while a row is
partial or pending.

Local validation for this branch: 11 Vitest tests, 4 Playwright tests, and 4
Python service/API tests pass; the production Vite build succeeds and `npm audit`
reports no vulnerabilities. Windows ARM64, packaged-host, and real-model checks
remain pending.

| ID | Existing behavior to preserve | React implementation | Evidence and remaining work | Status |
|---|---|---|---|---|
| APP-01 | Restore an existing dataset and keep the four workflow stages available | Last-used manifest is prioritized; four workflow stages remain available | React unit test and Playwright workflow case pass locally | Implemented; CI run pending |
| DATA-01 | Create a dataset from a local folder, scan recursively, and accept supported image extensions | Local folder source calls module 026 and stores a manifest | Python service tests use a synthetic image; browser import uses a mocked API | Implemented; real-folder browser-to-service E2E pending |
| DATA-02 | Ingest remote and iWISC task sources | No React API path yet | Port supported source adapters and verify offline/error/restart behavior | Pending |
| SET-01 | Configure annotation labels, classification labels, and the external tool | React settings call the module 012 process layer | Add save/reopen tests for each setting and tool override | Implemented; persistence test pending |
| LIST-01 | Search names, filter annotation/classification/review state, clear filters, and paginate | React list supports search, status, classification, low-confidence, label filters, and 20-item pages | Model/App tests and Playwright filter case pass; page-boundary and combined-filter cases remain | Implemented; coverage partial |
| WORK-01 | Draw, move, resize, delete, undo, and redo rectangle annotations; navigate between images | React Canvas supports drag drawing, moving, lower-right resizing, deletion, undo/redo, and image navigation | Canvas unit tests plus Playwright draw/save/switch/reopen against a mocked API pass; integrated API E2E remains | Implemented; integration pending |
| WORK-02 | Preserve existing polygons and other non-rectangle X-AnyLabeling shapes during rectangle edits | React conversion keeps non-rectangle shapes and their relative order | Model test and Python annotation round-trip test cover polygon preservation | Implemented; representative user-file fixture pending |
| WORK-03 | Apply and clear image-level classification with keyboard/quick actions | React classification control saves changes immediately | App test verifies classification filtering and setting a classification; add clear/next-image persistence cases | Implemented; coverage partial |
| TOOL-01 | Launch X-AnyLabeling, LabelMe, or ISAT for the selected image | Local API uses the existing adapter registry and accepts an optional executable path | Add adapter/API tests; verify launch on supported Windows architectures | Implemented; runtime verification pending |
| AI-01 | Run local YOLO pre-labeling on one image or a dataset with confidence and overwrite controls | React UI calls the local Python model path; service limits each request to 500 images | Test skip/overwrite, confidence, malformed model, empty prediction, and ARM64 runtime compatibility using an authorized test model | Implemented; model/runtime validation pending |
| LABEL-01 | Scan label counts, identify near duplicates, rename, merge, and delete labels | React label manager exposes these operations, displays affected-image counts, and confirms deletion | Python service test covers scan/rename/merge/delete and verifies paths are not returned; add merge-impact preview | Implemented; UI impact review partial |
| REVIEW-01 | Filter the QA queue and persist approve/reject status and comments | Review stage records per-image status; review confirmation is stored in browser local storage | App unit and Playwright approval cases pass; add restart and rejection/comment cases | Implemented; coverage partial |
| EXPORT-01 | Select COCO, YOLO, VOC, ImageFolder, or CSV; optionally split data; block unsafe export | React export panel checks completion, partial-export opt-in, review confirmation, and module 014 validation | App test covers both UI gates; real export/preflight integration and path failure cases remain | Implemented; export validation pending |
| LOCAL-01 | Keep local data inside the app boundary and reject unrelated image paths | Loopback API uses a session token, checks browser origin, and applies the dataset folder grant | Python service/API tests check token, origin, and path grant | Implemented; tests pass locally |
| HOST-01 | Open the app as a native desktop window with local file/folder pickers and controlled sidecar lifecycle | The React build is served by the loopback Python process; native host bridge is not included | Add a desktop host, native dialogs, process lifecycle, and packaged-app launch/restart tests | Pending |

## Required gates before claiming parity

1. Every required row above is implemented or explicitly removed from the product
   scope with an owner decision.
2. Browser tests cover the full create → annotate → save → close/reopen → review →
   export path on a synthetic dataset using the real local API, not only a mock.
3. Service tests cover malformed inputs, local path boundaries, and persisted
   annotations, classifications, review decisions, and exports.
4. The packaged desktop host starts the local service without opening a console,
   supports the intended Windows architectures, and passes the same workflow.

The test layers are kept beside the code: React unit tests in web/src/, browser
tests in web/e2e/, and Python service/API tests in tests/.
