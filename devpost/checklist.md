---
doc: checklist
status: draft
---

# Bugsy Build Checklist

Build mode: learn

## Slices

- [ ] **1. Upload or paste requirements and review the parsed preview**
  Becomes usable: A user can open Bugsy's dark dashboard, upload or paste `requirements.txt`, choose Python, see a read-only parsed preview, replace the source, and get line-specific feedback for malformed or unsupported input. Parsing remains distinct from compatibility.
  Why now: This gives us the first complete browser-to-API behavior and settles the supported requirements syntax before package analysis depends on it. It also bootstraps the React and FastAPI projects in the same slice.
  PRD ref: `prd.md > The Core Journey`, `prd.md > Intake and parsing`, `prd.md > States and Boundaries`
  Spec ref: `spec.md > Requirements parser`, `spec.md > React workflow UI`, `spec.md > FastAPI request and job API`, `spec.md > API Contract`, `spec.md > Folder Structure`
  Build: Create `backend/pyproject.toml`, `backend/app/main.py`, `backend/app/config.py`, `backend/app/api/routes/requirements.py`, `backend/app/schemas/requirements.py`, `backend/app/services/requirements_parser.py`, and `backend/tests/test_requirements_parser.py` plus `backend/tests/test_requirements_api.py`. Create `frontend/package.json`, `frontend/package-lock.json`, `frontend/index.html`, `frontend/tsconfig.json`, `frontend/vite.config.ts`, `frontend/src/main.tsx`, `frontend/src/App.tsx`, `frontend/src/api/client.ts`, `frontend/src/types/requirements.ts`, `frontend/src/components/RequirementsInput.tsx`, `frontend/src/components/ParsedPreview.tsx`, `frontend/src/styles.css`, and `frontend/src/App.test.tsx`. Add the frontend test script and test dependencies to `frontend/package.json`. Update `.gitignore` for project virtual environments, Node modules, caches, and build output; add `.env.example` with names only; add setup instructions to `README.md`. Install Bugsy's own backend packages only in `backend/.venv` and its own front-end packages only in `frontend/`. Never install or resolve the user's submitted packages on the host.
  Verify (mechanical): Run `uv sync --project backend` and `uv run --project backend pytest -q`; run `npm --prefix frontend install`, `npm --prefix frontend test -- --run`, and `npm --prefix frontend run build`. Inspect test output and confirm valid constraints/markers parse, unsupported lines block analysis with original line numbers, upload and paste preview work, and source replacement clears stale parse errors. No sandbox dependency operations occur in this slice.
  Learner check: Start the two local development servers, paste a small requirements file containing a pinned package and a valid marker, and review the parsed rows. Then add one unsupported line and confirm Bugsy names it and prevents analysis; replace the source and confirm parsing runs again.
  Commit: `Add requirements input and parsed preview`

- [ ] **2. Analyze dependencies with PyPI evidence and a real ConTree resolver**
  Becomes usable: A cleanly parsed file can be analyzed. Bugsy shows metadata evidence, the real resolver's result, conflict details, and an incomplete/unavailable state if ConTree cannot resolve it. The API and UI distinguish this analysis from sandbox installation and import checks.
  Why now: The resolver and evidence are the product's factual foundation. They must work before Nemotron is allowed to explain or propose a repair.
  PRD ref: `prd.md > Compatibility analysis and repair proposal`, `prd.md > States and Boundaries`
  Spec ref: `spec.md > Evidence and resolver engine`, `spec.md > ConTree sandbox runner`, `spec.md > Verification adapter contract`, `spec.md > Data Model`, `spec.md > Error Handling and Safety`
  Build: Add `backend/app/api/routes/analyses.py`, `backend/app/schemas/analysis.py`, `backend/app/domain/evidence.py`, `backend/app/services/pypi_metadata.py`, `backend/app/services/compatibility.py`, `backend/app/services/workflow.py`, and the version-specific implementation in `backend/app/adapters/contree.py`. Add `backend/app/adapters/verification.py` and a `backend/app/adapters/stub.py` that returns only explicitly simulated test results. Add mocked API/unit tests and resolver fixtures under `backend/tests/`. Update `test_sandbox.py` only as needed to show detected sandbox OS/Python for the harmless smoke check without printing credentials. Add typed analysis/report components to `frontend/src/api/client.ts`, `frontend/src/types/analysis.ts`, and `frontend/src/components/CompatibilityReport.tsx`; connect the parsed preview to the analysis state in `frontend/src/App.tsx`.
  Verify (mechanical): Run backend and frontend tests/build. Unit and API tests use deterministic metadata fixtures and explicitly labeled stub responses. Run a real, small resolver check only through the ConTree adapter; inspect the returned resolver version, sandbox OS/Python, exit status, and output. Confirm no user requirements are passed to a local resolver or installer. If ConTree fails, affected results must be not run/incomplete and no compatibility claim appears.
  Learner check: Analyze a small requirements file and inspect where each finding gets its evidence. If a real conflict is used for the check, verify the report names the resolver result and sandbox environment; if the sandbox is unavailable, confirm Bugsy reports analysis as incomplete rather than compatible.
  Commit: `Add evidence-backed compatibility analysis`

- [ ] **3. Add Nemotron explanation and a validated repair proposal**
  Becomes usable: Users can read a plain-language explanation grounded in the evidence and review a repair proposal with a summary and diff. Proposed versions are rejected unless evidence supports them and a real ConTree resolver run accepts the proposal.
  Why now: Once deterministic facts are available, the model can help explain them without becoming the source of package truth. Keeping proposal generation separate makes unsupported model output easy to catch.
  PRD ref: `prd.md > Compatibility analysis and repair proposal`, `prd.md > Product Decisions`
  Spec ref: `spec.md > Nemotron diagnosis provider`, `spec.md > API Contract`, `spec.md > Error Handling and Safety`, `spec.md > Data Model`
  Build: Add `backend/app/adapters/token_factory.py`, `backend/app/services/diagnosis.py`, `backend/app/services/repair.py`, and response schemas for explanation and proposal. Extend `backend/app/config.py` with server-side Token Factory settings and add tests for malformed model output, unsupported candidate versions, evidence IDs, output truncation, and unavailable model errors. Add the conflict explanation and proposal/diff UI in `frontend/src/components/ConflictExplanation.tsx` and `frontend/src/components/RepairProposal.tsx`, plus associated types and component tests. Keep credentials out of front-end configuration and test output.
  Verify (mechanical): Run `uv run --project backend pytest -q`, `npm --prefix frontend test -- --run`, and `npm --prefix frontend run build`. Use mocked model responses for repeatable tests; test a minimal live Token Factory response only if needed to verify a changed integration. Confirm every proposal candidate is evidence-linked and accepted by ConTree resolution before it is labeled ready; truncated or invalid model output must yield a clear unavailable/invalid state.
  Learner check: Review a generated or test-fixture proposal beside its evidence and diff. Confirm the model explanation is separate from the resolver result, and that an ungrounded candidate cannot become a ready proposal.
  Commit: `Add grounded repair proposals`

- [ ] **4. Verify proposals in ConTree and gate approved downloads**
  Becomes usable: A user can request verification, see separate resolution, installation, import, and project-test statuses with actual sandbox OS/Python, review the exact proposed diff, approve it, and then download the revised `requirements.txt`. The original input remains unchanged.
  Why now: This completes the end-to-end kernel by connecting an evidence-backed proposal to isolated checks and explicit user approval. The final slice also verifies the full journey and all failure states together.
  PRD ref: `prd.md > Nebius verification and results`, `prd.md > Proposed-file approval and download`, `prd.md > States and Boundaries`
  Spec ref: `spec.md > Verification adapter contract`, `spec.md > ConTree sandbox runner`, `spec.md > Report and approved download builder`, `spec.md > API Contract`, `spec.md > Error Handling and Safety`, `spec.md > Testing Strategy`
  Build: Complete the version-specific `backend/app/adapters/contree.py` workflow for fixed remote resolution, installation, and reliable import smoke checks; add `backend/app/api/routes/verifications.py`, approval/download routes, verification schemas and import-name allowlist. Add tests for independent statuses, captured failures, skipped imports, Python/environment mismatch, disabled sandbox, stub success/failure labels, stale approval hashes, and download gating. Complete `frontend/src/components/VerificationStatus.tsx`, `frontend/src/components/ApprovalDiff.tsx`, and download controls; connect them in `frontend/src/App.tsx`. Update `README.md` with local app setup, sandbox configuration, privacy notes, and the demo path.
  Verify (mechanical): Run all backend tests, all frontend tests, and the frontend production build. In ConTree only, verify a small safe proposal through resolution, installation, and reliable imports, capturing actual OS/Python and check output. Exercise a failed or skipped check and both stub outcomes in automated tests; every stub result must remain visibly simulated and must not claim compatibility. Confirm project tests always read `not run—project code not provided`, the original text is unchanged, and download is unavailable until approval of the exact diff hash.
  Learner check: Complete the full input-to-download journey. Inspect each check separately, compare the proposed diff to the original, approve it, download the revised file, and confirm the original file was not modified.
  Commit: `Complete isolated verification and approved download`

## Hands-on Checkpoints

- [ ] Early usable behavior explored — after slice 1, learner tries upload, paste, parsed preview, and malformed-line feedback; feedback can shape remaining UI.
- [ ] Final kick-the-tires exploration and feedback completed — after slice 4, learner tries the complete workflow and reports anything confusing or broken.

## Final Review

- [ ] Final review complete — feedback resolved and learner confirms ready to ship

## Code Tour and App Map

- [ ] Learning activity complete — guided route, focused alternative, prior practice connected, or brief recap
- [ ] Optional edit and transfer reflection addressed — offered/declined/already covered/not applicable as appropriate
- [ ] `devpost/app-map.html` generated from finished code, checked, and shown, including a project-grounded practice to reuse

Activity and evidence: [what actually happened; real document/test/code references; unfinished work if interrupted]
Route and stops: [actual paths and symbols; guided stops completed, or reference-only route]
Edit outcome: [tried/kept/reverted/declined/not applicable; verification if changed]
Reflection: [offered/answered/declined/already covered — personal answer belongs only in the ignored profile]
Activity mode: [live app and editor, explicit static fallback, focused alternative, prior practice, or recap]

## Revisions
