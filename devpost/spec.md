---
doc: spec
status: approved
---

# Bugsy — Technical Spec

## How This Works, In Plain Language

Bugsy has a web page and a Python API. The web page accepts a `requirements.txt` file or pasted text, lets the user choose a Python version, and shows a read-only preview. It sends that input to the API, which first checks that every line is supported. If a line cannot be interpreted safely, Bugsy stops and points to it instead of giving a partial answer.

For supported input, PyPI provides package metadata, and a real `uv` dependency resolution runs inside ConTree to see whether the selected Python and actual sandbox platform can resolve one compatible set. Nemotron receives those facts and may explain them or rank repair ideas. It does not choose a version from memory: every version in a proposal must be accepted by the resolver and have evidence attached.

When the user requests verification, the API sends the proposed requirements to Nebius ConTree, an isolated cloud environment. A fixed, application-owned sequence runs there: resolve, install, and attempt safe import smoke checks for packages with a reliable known import mapping. The model never writes a shell command for the host. The report shows each check separately, names the sandbox's actual OS and Python version, and always says project tests were not run because no project code was provided. After review and approval, the browser downloads a revised file; Bugsy never edits the original upload or a local project.

This is a single-user proof of concept with no account system. The backend persists sources, analyses, proposals, approvals, and verification-stage history/results in SQLite at `backend/data/bugsy.sqlite3`; a backend restart does not erase those records. A run interrupted while active is recovered as unknown/incomplete and is not automatically retried. The browser still holds transient view state.

## The Core Journey Through the System

Implements `prd.md > The Core Journey` and `prd.md > Requirements Input and Preview`, `Compatibility Analysis`, `Repair Proposal and Approval`, and `Verification Results`.

```mermaid
sequenceDiagram
  actor User
  participant UI as React + Vite browser
  participant API as FastAPI
  participant Parse as Requirements parser
  participant Facts as PyPI + uv evidence engine
  participant LLM as Nebius Token Factory (Nemotron)
  participant Sandbox as Nebius ConTree sandbox
  User->>UI: Upload/paste requirements.txt + choose Python
  UI->>API: POST /api/v1/requirements/parse
  API->>Parse: Parse supported PEP 508 lines
  Parse-->>UI: Read-only preview or line errors
  User->>UI: Start analysis
  UI->>API: POST /api/v1/analyses
  API->>Facts: Query metadata and run uv resolution
  Facts-->>API: Candidate facts, resolver result, evidence
  API->>LLM: Ask for explanation and grounded repair ranking
  LLM-->>API: Structured explanation (validated against evidence)
  API-->>UI: Compatibility report + proposal
  User->>UI: Request sandbox verification
  UI->>API: POST /api/v1/analyses/{id}/verification
  API->>Sandbox: Fixed verification workflow + proposed file
  Sandbox-->>API: Environment and per-check results/logs
  API-->>UI: Pollable job result
  User->>UI: Review diff, approve, download
  UI->>API: POST approval; then GET download
```

The user never chooses a target operating system. Analysis uses the selected Python version and supported marker logic; verification reports the actual sandbox environment. A successful sandbox result applies only to that environment and does not prove the user's local machine, other operating systems, or their application works.

## Stack

| Piece | Recommendation | Why and tradeoff |
|---|---|---|
| Browser app | React, TypeScript, Vite; plain CSS or CSS modules | Matches the requested stack and supports the report-heavy UI. The browser only calls Bugsy's API; it never receives service keys. |
| API | Python 3.12, FastAPI, Pydantic | Python fits packaging tools and the existing sandbox test. FastAPI validates request/response models and exposes `/docs` for development. |
| Requirement parsing | `packaging` requirement parser | Use standards-aware parsing rather than string splitting. Only explicitly supported `requirements.txt` lines proceed; unsupported directives block complete analysis. |
| Compatibility facts | PyPI JSON API plus `uv` running only inside a fresh ConTree environment | PyPI metadata is evidence; `uv` is the real resolver. Metadata alone is not an installation guarantee. No submitted dependency resolution or installation runs on the host. `uv` supports Python/platform targeting and markers, but alternate-platform resolution is best effort. |
| AI explanation | OpenAI-compatible Nebius Token Factory API; configurable Nemotron model ID | Keep the vendor-specific call behind `DiagnosisProvider`. A model explains evidence; deterministic code validates any recommendation. The currently available account model `nvidia/nemotron-3-super-120b-a12b` returned a successful completion in a short smoke request. |
| Real verification | Nebius ConTree client | The production adapter uses `contree-client==0.4.1` for fixed operations in ConTree. The separate root-level `test_sandbox.py` still uses `contree-sdk==0.3.6` for its standalone smoke test; that legacy script is not the production adapter. |
| Local adapter | An explicit test/development stub implementing the same adapter contract; no local dependency evaluation | May produce canned simulated success/failure states for UI and automated tests. Every result carries `execution_mode: simulated` and is visibly labeled simulated; it can never count as real verification. |
| Tests and source control | pytest, FastAPI TestClient/httpx, React test tooling, Git/GitHub | Unit and API checks can run without paid services. Keep recorded resolver fixtures for routine tests; separately run real resolver and opt-in integration smoke checks. |
| Hosting (optional) | Vercel static frontend + Render FastAPI service | Official docs support Vite on Vercel and FastAPI on Render. Separate hosting means CORS and production secrets must be configured. Local recording is sufficient for the hackathon; deployment is optional. |

Use lockfiles for JavaScript and Python dependencies. The live service checks below confirmed current account model IDs and a harmless inference call. Keep model ID configurable because account availability can change.

## Where It Runs and How Someone Tries It

### Local demo

- Requirements: Node.js LTS, Python 3.12 for Bugsy's backend, `uv`, Git, plus Nebius Token Factory and ConTree credentials for live AI and sandbox checks. The supported Sandbox targets are Python 3.10, 3.11, 3.12, 3.13, and 3.14, mapped respectively to `python:3.10-slim`, `python:3.11-slim`, `python:3.12-slim`, `python:3.13-slim`, and `python:3.14-slim`.
- Start the API from the repository root: `uv run uvicorn backend.app.main:app --reload --port 8000`.
- Start the UI in a second terminal: `npm --prefix frontend run dev`.
- Open the Vite URL printed in the terminal (normally `http://localhost:5173`). API docs are at `http://localhost:8000/docs`.
- Keep `.env` local and ignored by Git. Use `NEBIUS_API_KEY`, `NEBIUS_BASE_URL`, `NEBIUS_MODEL_ID`, `CONTREE_BASE_URL`, `CONTREE_PROJECT_ID`, `SANDBOX_ENABLED`, and `MAX_REQUIREMENTS_BYTES`. The target-specific image mapping is application-controlled; a global `SANDBOX_IMAGE` override must not silently select an image for a different target. Never print secret values in logs or browser responses.
- The current harmless ConTree smoke test succeeded in the existing `.venv`, and Token Factory model discovery plus a completion succeeded. Keep `SANDBOX_ENABLED` configurable; turn it off when the service becomes unavailable. The development stub may be used only in tests and must never report a real pass.

### Hackathon handoff

Submission still needs a short demo video and a public GitHub repository. Deployment is optional and does not replace either. If a live URL is useful, deploy the static frontend on Vercel and the API on Render; set `VITE_API_BASE_URL` at frontend build time and backend secrets in the Render service environment. Restrict CORS to the deployed frontend origin. Do not deploy until the live sandbox smoke test and secret configuration are confirmed.

## Look and Feel

Carry forward `prd.md > Look and Feel`: dark, professional developer-tool surface, crisp typography, restrained violet/rose accents, subtle bug-inspired mark, clear technical data. Keep the empty state honest and calm. Use evidence labels and separate status chips for resolution, installation, import check, and project tests. Do not show invented scores, fabricated reports, or a green “verified” badge based on only one check.

## Components

### React workflow UI

Owns upload/paste, Python version selection, read-only parsed preview, analysis/report, conflict explanation, repair diff, approval, download, and understandable loading/error states. It keeps transient view state in browser memory and sends no Nebius credentials to the client. Persisted workflow records are managed by the backend.

Implements `prd.md > Empty State`, `Requirements Input and Preview`, `Compatibility Analysis`, `Repair Proposal and Approval`, and `Verification Results`.

### FastAPI request and job API

Validates payloads, coordinates parsing/analysis/verification, returns stable JSON contracts, and persists workflow and stage state in SQLite. There is no login. On restart, persisted records remain available; operations found interrupted in a running state are reported as unknown/incomplete and are not replayed automatically.

Implements `prd.md > Requirements Input and Preview`, `Compatibility Analysis`, `Repair Proposal and Approval`, `Verification Results`, and `States and Boundaries`.

### Requirements parser

Uses `packaging` to parse requirement syntax and classify comments, supported environment markers, and supported options. Unsupported includes, editable/local/VCS references, index directives, or syntax outside the MVP's declared subset cause a blocking parse response with line number, original line, and actionable reason. Preserve original text byte-for-byte for the user-facing diff/download path. Do not silently drop any non-comment dependency line.

Implements `prd.md > Requirements Input and Preview` and `prd.md > States and Boundaries`.

### Evidence and resolver engine

Gets package and release metadata from PyPI, including `Requires-Python`, `Requires-Dist`, release files, and classifiers when available. For real resolver results, sends the original requirements into a fresh ConTree environment and runs a fixed `uv` resolution workflow there, using the selected Python version and actual sandbox platform. The API records resolver version/output and metadata source URL, package/version, field/value, and retrieval time. It never turns classifiers or metadata into proof of installability. Missing or contradictory metadata is reported as unknown. If ConTree is unavailable, the app can still show parsed input and metadata facts, but resolution and dependent compatibility conclusions are `not_run` or incomplete.

OS markers are evaluated for a concrete environment where possible. Since users do not select an OS and uv's alternate-platform resolution is best effort, the report must not claim cross-platform compatibility. The sandbox's actual environment is authoritative only for its own installation/import checks.

Implements `prd.md > Compatibility Analysis` and `prd.md > Conflict Explanation`.

### Nemotron diagnosis provider

Receives a compact structured bundle of resolver errors, metadata facts, environment values, and candidate versions. It returns plain-language explanation and a ranked repair plan in a strict JSON schema. A deterministic validator rejects any version not present in the evidence/candidate set or not accepted by a real resolver run. If the model is down, analysis facts remain visible and Bugsy reports that the explanation service is unavailable. Model output is never executed as a command.

Implements `prd.md > AI Diagnosis and Repair Planning`.

### Verification adapter contract

Expose one internal interface such as `verify(proposed_requirements, python_version) -> VerificationResult`. `ContreeVerificationAdapter` is the only adapter allowed to run real dependency resolution, package installation, and imports. The production adapter uses `contree-client==0.4.1`; the separate root smoke script's `contree-sdk==0.3.6` does not serve as a production fallback. A `StubVerificationAdapter` is for automated tests and UI development; it may produce canned success/failure outcomes, but every response is `execution_mode: simulated`, every simulated check is explicitly marked, and the UI labels it `SIMULATED` rather than presenting it as verification. It cannot make compatibility claims. Do not automatically switch to the stub when ConTree fails. The adapter contract isolates client-specific calls for independent testing and upgrades. It is not a second local resolver/installer.

Implements `prd.md > Verification Results` and `prd.md > Safety Requirements`.

### ConTree sandbox runner

Uses the production `contree-client==0.4.1` adapter to select the image mapped to the requested Python target, stage only the proposed requirements bytes, and call fixed application-authored operations. Check the actual interpreter before uv setup or resolution. Return detected OS/Python information, separate resolution, installation, and import smoke test results, exit codes, bounded logs, and timestamps. Commands are assembled from fixed templates and validated values; no model-generated shell string is accepted. Do not include project files or tests in the MVP.

Implements `prd.md > Verification Results` and `prd.md > Safety Requirements`.

### Report and approved download builder

Combines source, evidence, proposal, sandbox checks, and approval into a report. Download is enabled only after a proposal exists and the user approves the displayed diff. The generated file is returned as a download; the original upload is untouched.

Implements `prd.md > Repair Proposal and Approval` and `prd.md > Verification Results`.

## API Contract

All endpoints are proposed Bugsy application endpoints, not Nebius API routes. Prefix: `/api/v1`. Use JSON unless uploading a file. Pydantic request and response models mirror the shapes below.

### `POST /requirements/parse`

Accept either multipart form fields `file` (named `requirements.txt`) or `text` plus `python_version` as JSON. One source is required; enforce a small configurable size cap.

Request (paste):

```json
{"text":"numpy<2\npandas==2.2.0\n", "python_version":"3.12"}
```

Success `200`:

```json
{"source_id":"req_123", "parse_status":"parsed", "python_version":"3.12",
 "requirements":[{"line":1,"name":"numpy","specifier":"<2","marker":null},
                 {"line":2,"name":"pandas","specifier":"==2.2.0","marker":null}],
 "warnings":[], "source_sha256":"..."}
```

Blocking parse `422`:

```json
{"error":{"code":"UNSUPPORTED_REQUIREMENTS","message":"Fix the listed lines and upload or paste the file again.",
 "details":[{"line":3,"original":"-e ./local_pkg","reason":"Editable local paths are not supported in this MVP."}]}}
```

### `POST /analyses`

Request: `{"source_id":"req_123"}`. Starts metadata inspection, real resolution in ConTree, and optional Nemotron explanation. Response `202` includes `{"analysis_id":"ana_123","status":"queued","poll_url":"/api/v1/analyses/ana_123"}`. Source text is kept only in process memory for this demo.

### `GET /analyses/{analysis_id}`

Response is either a progress shape `{"analysis_id":"ana_123","status":"running","stage":"resolver"}` or a completed result:

```json
{"analysis_id":"ana_123","status":"complete","execution_mode":"real",
 "environment":{"requested_python":"3.12","os_target":null},
 "compatibility":{"status":"conflict","resolver":"uv","resolver_version":"...","summary":"..."},
 "evidence":[{"kind":"requires_python","package":"sample","version":"1.2.0","value":">=3.13","source_url":"https://pypi.org/pypi/sample/1.2.0/json","retrieved_at":"..."}],
 "conflicts":[{"packages":["a","b"],"reason":"...","evidence_ids":["ev_1"]}],
 "repair":{"status":"ready","requirements_text":"...","diff":"...","changes":[{"package":"a","before":"...","after":"...","evidence_ids":["ev_2"]}]},
 "explanation":{"status":"available","text":"..."},
 "checks":{"resolution":{"status":"passed"},"installation":{"status":"not_run"},"imports":{"status":"not_run"},"project_tests":{"status":"not_run","reason":"Project code not provided."}}}
```

The example values are schema illustrations only; no package result or compatibility score is prepopulated.

### `POST /analyses/{analysis_id}/verification`

No model commands accepted. Request: `{"proposal_id":"rep_123"}`. If `SANDBOX_ENABLED` is false or credentials are missing, return `503 SANDBOX_UNAVAILABLE` and do not present a successful result. Otherwise return `202` with `verification_id` and `poll_url`.

### `GET /verifications/{verification_id}`

Return `queued`, `running`, `complete`, or `failed`, with `execution_mode` (`real` or `simulated`), actual `environment.os`, `environment.python_version` when real, `sandbox_provider`, and the separate check objects. Each check has `status` (`passed`, `failed`, `not_run`, or `simulated`), optional `simulated_outcome` (`passed` or `failed`), `reason`, `exit_code`, and bounded `output`. In simulated mode, show a persistent `SIMULATED — no sandbox checks ran` label and display outcomes as `Simulated pass` or `Simulated failure`; never use a real passed/failed badge. `project_tests` always has `status: not_run` and reason `Project code not provided.`

### `POST /analyses/{analysis_id}/approval` and `GET /analyses/{analysis_id}/download`

Approval request: `{"proposal_id":"rep_123","approved":true,"diff_sha256":"..."}`. The API verifies that the hash matches the displayed proposal. Download returns `text/plain` with `Content-Disposition: attachment; filename="requirements.txt"`. Reject if proposal is missing, changed since display, or not approved.

### Shared error envelope

```json
{"error":{"code":"SERVICE_UNAVAILABLE","message":"Dependency metadata could not be reached. Try again.","request_id":"...","retryable":true}}
```

Use `400` for invalid request shape, `413` for oversized input, `404` for unknown IDs, `409` for a workflow state mismatch, `422` for unsupported requirement lines, `429` for upstream throttling, `503` for disabled/missing integrations, and `500` only for unexpected server failures. Never send secrets, stack traces, full environment variables, or unbounded raw logs to the browser.

## Data Model

- `RequirementSource`: random ID, original text, SHA-256, Python version, parsed line records, creation time. Persisted in SQLite; never overwritten by a proposal.
- `ParsedRequirement`: line number, normalized distribution name, specifier, extras, marker, original line. Keep original line for exact feedback and review.
- `EvidenceItem`: evidence ID, package/version, source URL, field/value, resolver/source version, retrieval timestamp. Evidence items ground both explanation and proposed changes.
- `Analysis`: ID, status/stage, source ID, environment assumptions, compatibility result, conflicts, evidence IDs, optional explanation, optional proposal. Persisted in SQLite.
- `RepairProposal`: ID, complete revised requirements text, unified diff, individual changes with evidence IDs, content hash, approval state. Download requires an approval for the exact hash.
- `VerificationJob`: ID, proposal ID, execution mode (`real`/`simulated`), job stage, actual sandbox environment when real, four independent check results, timestamps, bounded diagnostics. Stage history and one-shot approval consumption are persisted in SQLite. Project tests always `not_run`.

SQLite is the local persistence layer for source text, analyses, proposals, approvals, and verification history/results. The browser may retain transient view state during its open session; do not put uploads in browser local storage by default. An interrupted operation remains unknown/incomplete after restart until new evidence exists; recovery never recreates a consumed approval or automatically retries the operation.

## Error Handling and Safety

- Malformed or unsupported requirement lines stop full analysis and return original lines with line numbers and feedback. Valid PEP 508 markers are preserved and evaluated where supported.
- PyPI/network/model errors are distinguished from a real incompatibility. Missing evidence means `unknown` or `analysis_incomplete`, not compatible.
- Resolver and sandbox command timeouts, output length, concurrency, and uploaded file size are bounded. Keep each run in a fresh sandbox environment and discard it after reporting. No `uv` resolution or submitted-package installation runs on the backend host.
- Only fixed application-owned commands run in ConTree. Never run model-created commands on the host. Submitted requirements are untrusted input and are installed only in the isolated sandbox; use an allowlist for supported syntax, no secrets in sandbox, and restrict upload size/package count/time.
- The historical root `test_sandbox.py` harmless smoke command succeeded; this does not establish production workflow or image availability. Runtime checks still fail closed when ConTree becomes unavailable: affected real checks are `not_run` or incomplete and no compatibility claim is made. The stub is selected explicitly for development/tests; it may simulate success or failure, but every response is visibly simulated and never real verification. It is never an automatic fallback.
- Supported targets map explicitly: Python 3.10 → `python:3.10-slim`, 3.11 → `python:3.11-slim`, 3.12 → `python:3.12-slim`, 3.13 → `python:3.13-slim`, and 3.14 → `python:3.14-slim`. These are configured image references; provider availability and the actual runtime have not been verified for every target. Check the Sandbox's actual `sys.version` before resolver setup or execution. Missing, malformed, or mismatched runtime information must produce an incomplete/error result and can never produce successful resolution. Report the effective image and actual runtime when available; do not claim local or cross-platform support.
- Keep API keys server-side in local environment variables and production secret stores; `.env` is ignored by Git. Redact tokens from request errors and logs. Use HTTPS in deployment, tight CORS, upload limits, safe filenames, random non-guessable IDs, and no user-controlled filesystem paths.
- Treat model output as untrusted structured text. Validate JSON schema, escape it in the UI, verify every version against evidence plus resolver, and reject unsupported claims. No direct shell execution.
- Download only after explicit approval of the exact diff; preserve the original and never write to a user's local project.

## Testing Strategy

The following are implementation acceptance checks, not claimed as already run:

1. `pytest` unit tests for supported requirements syntax, comments, extras, version specifiers, valid environment markers, malformed/unsupported line blocking, normalization, evidence formatting, candidate validation, diff generation, and status transitions.
2. FastAPI API tests for paste/upload parsing, size/type validation, every error envelope, analysis lifecycle, proposal hash approval, download gating, disabled sandbox, expired IDs, and project tests always `not_run`.
3. Resolver checks use recorded fixtures for known success/conflict report behavior; live resolver integration tests run `uv` in ConTree against PyPI. Never make every unit test depend on network access and never spawn submitted dependency resolution on the host.
4. Adapter contract tests: stub simulates both success and failure and the UI/API always preserve the `simulated` label; ConTree adapter is tested with a harmless `print` smoke command before resolver and dependency installation work. No package repair command may run on the host.
5. Sandbox smoke/import checks: verify the harmless command; then known small requirements; then import mapping behavior. Capture resolution, installation, imports separately. Use allowlisted distribution-to-module mappings only where reliable; otherwise report skipped with reason.
6. Frontend tests for empty/upload/paste/preview/errors/report/diff/approval/download states and truthful status rendering. Manual end-to-end demo with live keys only after configuration is confirmed.

## Folder Structure

```text
Bugsy/
├── backend/
│   ├── app/
│   │   ├── main.py                 # FastAPI app, CORS, health route
│   │   ├── api/routes/             # parse, analysis, verification, approval/download
│   │   ├── schemas/                # Pydantic requests, responses, error models
│   │   ├── domain/                 # requirement, evidence, proposal, check models
│   │   ├── services/
│   │   │   ├── requirements_parser.py
│   │   │   ├── compatibility.py    # PyPI facts + sandbox resolver adapter calls
│   │   │   ├── diagnosis.py        # Nemotron prompt/schema/validation
│   │   │   ├── repair.py           # candidate validation + diff
│   │   │   └── workflow.py         # orchestration and SQLite-backed workflow state
│   │   ├── adapters/
│   │   │   ├── token_factory.py
│   │   │   ├── verification.py     # adapter interface
│   │   │   ├── contree.py          # live isolated sandbox adapter
│   │   │   └── stub.py             # tests/dev only; never a real pass
│   │   └── config.py               # environment configuration and limits
│   ├── tests/                      # unit, API, adapter contract, fixtures
│   └── pyproject.toml              # pinned Python tool/dependency config
├── frontend/
│   ├── src/
│   │   ├── api/                    # typed Bugsy API client
│   │   ├── components/             # upload, preview, evidence, diff, statuses
│   │   ├── features/               # analysis and verification workflow
│   │   ├── types/                  # API/domain TypeScript types
│   │   ├── App.tsx
│   │   └── styles/                 # dark theme tokens and CSS
│   ├── tests/
│   ├── package.json
│   └── vite.config.ts              # dev API proxy
├── devpost/                        # approved planning docs and this spec
├── .env.example                    # variable names only, no secret values
├── .gitignore                      # .env, uploads, caches, build outputs
└── README.md                       # setup, safety, demo workflow
```

## External Services and Current Documentation Check

### PyPI

Official JSON API documents `GET https://pypi.org/pypi/{project}/{version}/json` for a release; the project endpoint is `GET https://pypi.org/pypi/{project}/json`. Read metadata but treat the resolver and actual sandbox as distinct evidence. No key is required; network/rate limiting can still fail. [PyPI JSON API docs](https://docs.pypi.org/api/json/)

### uv

Run the supported `uv` CLI through the ConTree SDK's fixed remote command interface with validated arguments, a fresh sandbox workspace, timeouts, and captured output. Never call local `subprocess` for submitted dependency resolution or installation. `uv pip compile` can resolve requirements for a chosen Python/platform; uv documents best-effort limits for alternate platforms and marker complexity. Do not interpolate user/model strings into a shell. [uv resolution docs](https://docs.astral.sh/uv/concepts/resolution/) · [uv CLI reference](https://docs.astral.sh/uv/reference/cli/)

### Nebius Token Factory / Nemotron

Current public Nebius documentation shows an OpenAI-compatible API, base URL `https://api.tokenfactory.us-central1.nebius.com/v1/`, and chat completions through the official OpenAI Python client. The live authenticated `GET https://api.tokenfactory.nebius.com/v1/models` request succeeded and returned these Nemotron IDs for the configured account: `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`, `nvidia/Nemotron-3_5-Lightning`, `nvidia/Nemotron-3-Ultra-550b-a55b`, `nvidia/nemotron-3-super-120b-a12b`, and `nvidia/Nemotron-3.5-Super-VL`. A short `POST /v1/chat/completions` request to `nvidia/nemotron-3-super-120b-a12b` returned final text `OK`. Therefore use that ID as the current configured default while keeping `NEBIUS_MODEL_ID` configurable. [Nebius Nemotron quickstart](https://nebius.com/services/token-factory/nemotron) · [Token Factory API reference](https://api.tokenfactory.nebius.com/docs)

The inference smoke also showed that a very small output cap can be exhausted by Nemotron reasoning before it returns final text (`finish_reason: length`, null content). The app must check `finish_reason`, reject missing/incomplete content, and use a modest tested output budget rather than treating any HTTP 200 as a diagnosis. The smoke prompt contained no project or requirements data. The API key itself was not printed or stored in the spec.

Documented inference request shape:

```http
POST https://api.tokenfactory.nebius.com/v1/chat/completions
Authorization: Bearer <NEBIUS_API_KEY>
Content-Type: application/json
```

```json
{"model":"<NEBIUS_MODEL_ID>","messages":[{"role":"system","content":"Return schema-valid JSON grounded only in supplied evidence."},{"role":"user","content":"<structured evidence bundle>"}],"temperature":0.1}
```

Use the OpenAI client response's `choices[0].message.content`, then parse and validate it. Exact supported parameters and the response schema must be confirmed against the current API reference/model during the harmless model-connection build step. The published quickstart confirms the base URL and chat-completions pattern; it does not prove this account's key, model entitlement, quota, or price. Do not send private project code (none is collected in this MVP).

### Nebius ConTree

The production adapter uses the public `contree-client==0.4.1` API with separate operation submission and tracking. Its fixed operations, byte-backed file staging, bounded output, timeout handling, and retry policy are isolated in `backend/app/adapters/contree.py`. The separate root-level `test_sandbox.py` remains a standalone smoke script using `contree-sdk==0.3.6`; its SDK path is not used by the production adapter. [ConTree SDK repository](https://github.com/nebius/contree-sdk) · [ConTree SDK docs](https://docs.contree.dev/sdk/)

The historical root `test_sandbox.py` smoke test succeeded using `contree-sdk==0.3.6`; this confirms that standalone script's harmless command only. The production adapter now uses `contree-client==0.4.1`. Prior live production attempts reached file staging but did not establish successful production resolution. Provider image/runtime availability remains unverified for every configured Python target. Do not generalize the legacy smoke result to all production operations or all target images.

### Hosting

Vercel documents Vite frontend deployment, and Render documents FastAPI web-service deployment with a Python start command. This is an optional direction, not a deployment requirement; hosting plans, current pricing, cold starts, and environment settings must be rechecked if the user chooses deployment. [Vercel Vite docs](https://vercel.com/docs/frameworks/frontend/vite) · [Render FastAPI docs](https://render.com/docs/deploy-fastapi)

## What Was Simplified and Why

- One `requirements.txt` format, one selected Python version, one sandbox environment, and one Nemotron diagnosis call keep the proof focused on the unique kernel: evidence-backed repair plus real isolated verification.
- SQLite persistence keeps proposals, approval consumption, and verification-stage history across backend restarts without adding a separate database service or login. Interrupted active operations remain unknown/incomplete and are not automatically replayed.
- An explicit test stub keeps UI and automated test development moving when Nebius is unavailable; it can simulate success or failure but must clearly label every outcome as simulated and cannot resolve, install, import, or claim real compatibility.
- `uv` supplies the resolver instead of building a second resolver. PyPI metadata helps explain decisions but does not replace resolution or installation.
- Project code and tests are absent, so project tests always report `not_run` with `Project code not provided.`
- Manual package entry, other dependency formats, multi-agent orchestration, local full verification, and direct project edits remain out of scope per `scope.md`.

## Decisions and Open Issues

### Carried forward from the learner

- React + TypeScript + Vite; Python + FastAPI; NVIDIA Nemotron through Nebius Token Factory; PyPI evidence plus a real resolver; pytest/API/resolver/smoke testing; Git/GitHub; env-based secrets; optional frontend/backend hosts; and separate local development and live sandbox adapters.
- Approved product boundary and UX are in `scope.md` and `prd.md`; this spec does not add user-facing features.

### Decisions and remaining recommendations

- **Learner decision:** the local adapter is a test stub only. It may simulate success/failure for UI and automated tests, always with a visible simulated label. Real resolution, installation, and import checks run only in ConTree. On service failure, report affected real checks as not run/incomplete; never claim compatibility. The production ConTree adapter uses `contree-client==0.4.1`; the separate root smoke script still uses `contree-sdk==0.3.6`.
- SQLite persists workflow and verification state locally; interrupted runs are recovered as unknown/incomplete, with consumed approvals preserved and no automatic retry.
- Use Vercel + Render only if deploying; run locally for the required demo if no public link is needed.

### Useful unknown and planned check

Python 3.10–3.14 image mappings are present in code, but actual provider availability/runtime has not been confirmed for every image. Keep the runtime check fail-closed and validate each target through a separately approved live test before claiming it is available.

### Open questions

- Does the learner want optional deployment for a shareable live URL, or should the first demo remain local? Either choice still requires a short demo video and a public GitHub repository.
- Current live Token Factory model list, configured key authentication, a short chat completion, ConTree harmless execution, and installed SDK versions have been checked. Exact account quota/cost and the sandbox's detected OS/Python remain unverified; capture those before enabling full dependency verification. These are external conditions, not reasons to invent a model ID or sandbox route.

## References

- [FastAPI file uploads](https://fastapi.tiangolo.com/tutorial/request-files/) and [background tasks](https://fastapi.tiangolo.com/tutorial/background-tasks/)
- [PyPA `packaging` requirement parsing](https://packaging.pypa.io/en/stable/requirements.html)
- [Nebius Token Factory Nemotron API quickstart](https://nebius.com/services/token-factory/nemotron)
- [Nebius Token Factory API reference](https://api.tokenfactory.nebius.com/docs)
- [ConTree SDK](https://github.com/nebius/contree-sdk)
- [uv resolver and environment marker behavior](https://docs.astral.sh/uv/concepts/resolution/)
- [React](https://react.dev/), [TypeScript](https://www.typescriptlang.org/docs/), [Vite](https://vite.dev/guide/)
- [pytest](https://docs.pytest.org/en/stable/), [Git](https://git-scm.com/doc), [GitHub](https://docs.github.com/)
