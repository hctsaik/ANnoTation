# Streamlit-to-React Migration Test Cases

This is the acceptance matrix for the React rewrite. The existing Streamlit
application and its [E2E scorecard](annotation-app-e2e-scorecard.md) describe the
behavior being migrated; their results do not count as React verification.

## Current status

The React branch has a working local-folder workflow and a first Canvas editor.
“Implemented” means a user path exists in the code. It does not mean the full
legacy scenario has passed end-to-end. Release parity remains open while a row is
partial or pending.

Local validation for this branch: 15 Vitest tests, 6 Playwright tests (including
real local-folder and synthetic iWISC → React → Python service journeys), and
179 Python tests pass; the production Vite build succeeds. Windows ARM64,
packaged-host, target-Service, and real-model checks remain pending.

| ID | Existing behavior to preserve | React implementation | Evidence and remaining work | Status |
|---|---|---|---|---|
| APP-01 | Restore an existing dataset and keep the four workflow stages available | Last-used manifest is prioritized; four workflow stages remain available | React unit test and Playwright workflow case pass locally | Implemented; CI run pending |
| DATA-01 | Create a dataset from a local folder, scan recursively, and accept supported image extensions | Local folder source calls module 026 and stores a manifest | `web/e2e/local-service.spec.ts` creates a dataset from a real temporary folder containing root and nested images through the built React app and Python service; service tests cover image and annotation round trips | Implemented; real-folder and recursive browser-to-service path passes; format matrix and CI evidence pending |
| DATA-02 | Ingest remote and iWISC task sources | React can list and download remote Service datasets through modules 019/026; it can register an iWISC connection, list and claim tasks, load images locally, save React sidecars into the linked task record, and complete a task locally with a read-only final state | `web/e2e/iwisc-source.spec.ts` drives the built React app through the real local API and synthetic HTTP iWISC service, including API-key handling, claim, ZIP download, annotation save, completion, and read-only state; Python service/API tests cover task-record persistence, delivery status, and mutation lockout; current REST connector reports external result delivery as unsupported; target Service validation remains | Partial; synthetic local journey passes, external result delivery is unsupported by the current REST connector and target Service behavior remains unverified |
| SET-01 | Configure annotation labels, classification labels, and the external tool | React settings call the module 012 process layer | The real-service browser journey saves annotation/classification labels and verifies an applied classification after service restart; external tool override save/reopen remains | Implemented; persistence coverage partial |
| LIST-01 | Search names, filter annotation/classification/review state, clear filters, and paginate | React list supports search, status, classification, low-confidence, label filters, and 20-item pages | Model/App tests and Playwright filter case pass; App tests cover the 20/21-item boundary, disabled page controls, combined search/status/classification/confidence filters, review-status plus annotation-label filters, and returning to page one or clearing review filters | Implemented; local UI coverage complete; CI and packaged-host evidence pending |
| WORK-01 | Draw, move, resize, delete, undo, and redo rectangle annotations; navigate between images | React Canvas supports drag drawing, moving, lower-right resizing, deletion, undo/redo, and image navigation | `web/e2e/local-service.spec.ts` exercises draw, move, resize, delete, undo, and redo through the built React app, saves through the real API, switches images, restarts the service, and verifies the sidecar-backed rectangle is read back; representative user-file fixture and CI evidence remain | Implemented; synthetic real-service editor journey passes locally; broader fixture and CI evidence pending |
| WORK-02 | Preserve existing polygons and other non-rectangle X-AnyLabeling shapes during rectangle edits | React conversion keeps non-rectangle shapes and their relative order | Model test and Python annotation round-trip test cover polygon preservation | Implemented; representative user-file fixture pending |
| WORK-03 | Apply and clear image-level classification with keyboard/quick actions | React classification control saves changes immediately | The real-service browser journey applies a classification, clears it, switches to another image and back, then restarts the service and verifies the cleared value remains empty | Implemented; local real-service apply/clear persistence journey passes; CI and packaged-host evidence pending |
| TOOL-01 | Launch X-AnyLabeling, LabelMe, or ISAT for the selected image | Local API uses the existing adapter registry and accepts an optional executable path | Add adapter/API tests; verify launch on supported Windows architectures | Implemented; runtime verification pending |
| AI-01 | Run local YOLO pre-labeling on one image or a dataset with confidence and overwrite controls | React UI calls the local Python model path; service limits each request to 500 images | Test skip/overwrite, confidence, malformed model, empty prediction, and ARM64 runtime compatibility using an authorized test model | Implemented; model/runtime validation pending |
| LABEL-01 | Scan label counts, identify near duplicates, rename, merge, and delete labels | React label manager previews source file-operation counts, box/classification counts, and target usage before confirming a merge; it displays affected-image counts and confirms deletion | web/src/App.test.tsx covers the preview and cancel/confirm choice; web/e2e/local-service.spec.ts runs the preview, cancels once, confirms once, and verifies the real Python service rewrites persisted sidecars. Existing Python service tests cover scan/rename/merge/delete and path privacy | Implemented; local visible real-service merge journey passes; CI and packaged-host evidence pending |
| REVIEW-01 | Filter the QA queue and persist approve/reject status and comments | Review stage records per-image status and comment; review confirmation is stored in browser local storage | The real-service browser journey saves an approval and comment, restarts the service, and verifies both status and comment in React; rejection and CI evidence remain | Implemented; coverage partial |
| EXPORT-01 | Select COCO, YOLO, VOC, ImageFolder, or CSV; optionally split data; block unsafe export | React export panel checks completion, partial-export opt-in, review confirmation, and module 014 validation | The real-service browser journey explicitly allows partial export, confirms review, generates COCO output, checks its image and box contents, and verifies export history after service restart; other formats, splits, and path-failure cases remain | Implemented; synthetic COCO integration passes locally; broader format and CI evidence pending |
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
