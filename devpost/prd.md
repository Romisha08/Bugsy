---
doc: prd
status: approved
---

# Bugsy — Product Requirements

Bugsy helps developers, Python learners, and hackathon participants understand and repair dependency problems from an existing `requirements.txt`, then verify dependency checks in a Nebius sandbox.
Source: `scope.md > Who It's For`, `The Unique Kernel`, and `The Core Loop`.

## The Core Journey

1. The user opens Bugsy and sees a clean empty dashboard with a clear way to upload or paste a `requirements.txt`, choose a Python version, and learn what Bugsy checks.
2. Bugsy parses the file and shows a read-only preview of package names and version constraints. If any line cannot be interpreted reliably, Bugsy shows its line number, original text, and explanation, then asks the user to replace the file. Full compatibility analysis stays paused until the replacement parses cleanly.
3. After a clean parse, the user starts compatibility analysis. Bugsy examines package metadata and constraints, uses a real dependency resolver, and presents findings with evidence. Supported OS-specific markers are accounted for. No OS target selector is offered.
4. The user inspects conflicts and explanations, then reviews a proposed package-version or Python-version change. The proposal is clearly labeled as proposed, not verified.
5. The user requests verification. Bugsy checks resolution, installation, and reliable import smoke tests inside Nebius and shows each result separately. The report displays the actual sandbox OS and Python version. Project-specific tests are always marked “not run—project code not provided” in the MVP.
6. The user reviews a clear change summary and `requirements.txt` diff. After the proposal is ready and the user approves it, Bugsy enables download of the revised file. The uploaded or pasted source is preserved and never changed directly.

Source: `scope.md > The Core Loop`, `What "Working" Looks Like`, and `Safety Requirements`.

## Screens and Layout

The product is an analysis workspace, not a chat interface. The dashboard progresses through input, parsed requirements, analysis, repair, verification, and diff review states. The main analysis surface prioritizes dependency findings and technical evidence; AI explanations support those findings.

### 1. Empty dashboard and input
- Bugsy wordmark with a small, tasteful bug-inspired visual.
- Prominent “Upload requirements.txt” action and drag-and-drop area.
- Secondary option to paste `requirements.txt` contents.
- Python-version selection. No Windows/macOS/Linux target selector.
- Short workflow explanation: analyze dependencies → get evidence → get repair suggestions → verify in Nebius.
- Note: Project-specific tests are outside the MVP and will show “not run—project code not provided.”
- Show no compatibility scores, sample conflict results, or completed verification state before actual analysis.

### 2. Parsed requirements review
- Read-only list of parsed package names and version constraints.
- A clear parse status, separate from the later compatibility result.
- For malformed or unsupported lines Bugsy cannot reliably interpret, show line number, original line, plain-language explanation, and ask the user to upload or paste a corrected file. Block full compatibility analysis until the file parses cleanly.
- Allow replacing the source and parsing again; no inline editing.
- Do not flag valid supported syntax or supported environment markers as errors.

### 3. Compatibility report and conflict explanation
- Show whether the file was analyzed and summarize resolver findings without invented scores.
- For each conflict, show the affected packages and constraints, relevant Python requirement or supported environment marker, and the package metadata or resolver evidence behind the finding.
- Explain why the constraints cannot be satisfied together in plain language.
- Show compatible package-version or Python-version proposals with the evidence that supports them.
- When no conflict is found, say so for the analyzed environment; do not imply all platforms are compatible.
- Do not present a generic chat thread as the primary results surface.

### 4. Proposed repair and verification status
- Present proposed changes distinctly from results that the sandbox has verified.
- Let the user request a Nebius verification run for a proposal.
- Show the actual sandbox operating system and Python version used. If the actual Python version differs from the requested version, make the difference clear and do not describe the requested version as verified.
- Report four separate statuses: dependency resolution, package installation, import smoke test, and project tests.
- Run an import smoke test only when the distribution-to-import name mapping is reliable; otherwise show the import check as skipped.
- In the MVP, project tests always show “not run—project code not provided.” Successful dependency checks do not prove the user's application works.
- Show captured errors and clearly label passed, failed, skipped, and not-run checks.

### 5. Change review and download
- Show a concise summary and readable diff of proposed changes to the original requirements.
- Require explicit user approval before enabling the revised-file download.
- Preserve the original upload or pasted content; do not modify the user's local project or source file.
- Keep verification status beside the proposal so a proposed change cannot be mistaken for a verified fix. A failed, skipped, or unavailable check remains visible when the user reviews the diff.

Source: `scope.md > In-Scope Features`, `Safety Requirements`, and `Acceptance Criteria`.

## Look and Feel

A clean, professional dark developer-tool interface with clear typography and readable technical information. Use a deep charcoal or blue-black base, high-contrast text, and restrained rose or lilac accents for a subtly feminine touch. Use small, minimal bug-inspired details rather than a cartoon mascot. Reserve strong color for status meaning, and use a monospace face for package names, version constraints, and diff content. Avoid generic chat-app styling and decorative visual noise.

The dashboard concept in `prd.html` is a design proposal for review, not product implementation.

## Features and Behavior

### Intake and parsing

**User story:** As a developer with a dependency file, I want to preview what Bugsy understood before analysis so I can catch unsupported or malformed requirements.

- [ ] Users can upload or paste a `requirements.txt` and select a Python version.
- [ ] Bugsy shows parsed package names and constraints read-only before compatibility analysis.
- [ ] A malformed or unsupported line that cannot be reliably interpreted displays its line number, original content, and a clear explanation; full analysis pauses and asks the user to replace or correct the source file.
- [ ] Users can replace the file and parse again; they cannot edit parsed rows inline.
- [ ] Valid supported requirement syntax and environment markers are not incorrectly reported as malformed.
- [ ] Parse success and compatibility success are separate statuses.

### Compatibility analysis and repair proposal

**User story:** As a Python user facing a dependency conflict, I want to see why the packages do not fit and what evidence supports a possible change so I can choose whether to verify it.

- [ ] Bugsy uses package metadata and a real resolver to identify conflicts for the selected Python version and actual analysis environment.
- [ ] Findings identify affected packages and constraints and explain the conflict in plain language.
- [ ] Supported OS-specific requirement markers are considered using the actual environment where supported; there is no OS target selector.
- [ ] Compatible package or Python-version changes are presented as proposals with supporting compatibility evidence.
- [ ] Nemotron through Nebius Token Factory supports diagnosis and repair planning; it does not replace metadata or resolver evidence.
- [ ] No conflict is reported only for the analyzed environment; Bugsy makes no untested cross-platform claim.

### Nebius verification and results

**User story:** As a user considering a repair, I want Bugsy to check the proposed requirements in isolation and report exactly what passed, failed, or could not run.

- [ ] Before dependency operations are added, the Nebius SDK is confirmed with a harmless Python command.
- [ ] Resolution, installation, and import checks run in the Nebius sandbox; model-generated commands never run directly on the host.
- [ ] The user sees the actual sandbox OS and Python version. If the selected Python version differs from the actual version, the mismatch is explicit and the requested version is not described as verified.
- [ ] The report shows separate statuses for dependency resolution, package installation, import smoke test, and project tests.
- [ ] Import checks run only for reliably mapped import names; otherwise they are marked skipped.
- [ ] Project tests show “not run—project code not provided” because code and test uploads are outside the MVP.
- [ ] Errors are captured and shown. A successful install or import does not by itself yield “fully verified” and never implies the user's application works.
- [ ] If sandbox integration is disabled or unavailable, the report identifies checks as unavailable or not run and does not claim sandbox verification.

### Proposed-file approval and download

**User story:** As a user who wants to apply a repair, I want to inspect the exact changes and approve them before downloading a revised requirements file.

- [ ] Bugsy shows a summary and diff against the original file.
- [ ] The proposal's status is distinct from sandbox verification status.
- [ ] Download remains disabled until a repair proposal is ready and the user approves the displayed changes.
- [ ] After approval, Bugsy offers a revised `requirements.txt` download and preserves the original input unchanged.

## States and Boundaries

- **First use:** Show the empty dashboard and workflow explanation, without fabricated analysis or verification results.
- **Malformed or unsupported requirement:** Identify line number, original text, and reason; pause full compatibility analysis and ask for a corrected source.
- **Parsed successfully:** Show the parsed requirements preview. This does not imply compatibility.
- **Analysis complete, conflicts found:** Show conflict explanations, evidence, and proposals.
- **Analysis complete, no conflicts found:** Report no conflict found for the analyzed environment only.
- **Resolver or metadata problem:** Show the failed analysis step and captured error; do not substitute a made-up compatibility result.
- **Sandbox disabled or unavailable:** Continue where possible, label verification unavailable or not run, and leave the proposal unverified.
- **Verification complete:** Show all four check statuses independently; project tests are “not run—project code not provided.” Skipped or failed checks prevent an unqualified fully-verified claim.
- **Proposal ready, not approved:** Show summary and diff; keep download disabled.
- **Proposal approved:** Enable download; the original source remains unchanged.
- **Project-specific tests:** Not run in this MVP. Dependency resolution, installation, and import checks do not establish whether the user's application works.

## Product Decisions

- The MVP accepts uploaded or pasted `requirements.txt` only. Separate manual package entry and other dependency formats are deferred.
- Users choose a Python version, but do not choose an OS target. Bugsy displays the actual sandbox OS and Python version and limits claims to that environment.
- The parsed requirements preview is read-only. Malformed or unsupported lines block full compatibility analysis until the source is replaced and parses cleanly.
- Verification runs in Nebius; sandbox integration is behind a configuration flag. A harmless SDK command is the first sandbox milestone.
- Project code and test uploads and project-specific test execution are out of the MVP. The project-test status is “not run—project code not provided.”
- Bugsy preserves the original source and provides a revised file only after the proposal is ready and the user approves its summary and diff.
- Project-specific test support may be added later. Its product extension is recorded here; architecture choices belong in `4-spec`.
- Visual direction: professional dark developer interface, clear technical typography, restrained rose/lilac accents, and subtle bug-inspired details.

## What We're Building

A compact workflow for uploading or pasting a `requirements.txt`, selecting a Python version, reviewing parsed requirements, understanding evidence-backed compatibility findings, requesting isolated Nebius checks, and approving a clearly labeled revised file download.

## Deferred From the POC

- Project-code and test-file upload and project-specific testing: they add another input and execution workflow; dependency checks alone are the MVP proof.
- Separate manual package entry and `pyproject.toml` support: the first parser and workflow focus on `requirements.txt`.
- OS target selection and claims of compatibility on other platforms: the MVP reports the actual sandbox environment only.
- Direct modification of the user's uploaded or local file: the product returns a revised download after approval.

## Possible Later Enhancements

Project-code and test uploads with isolated project-specific test execution may be considered after the dependency workflow works reliably. Manual package entry and `pyproject.toml` support may also be considered later.

## Non-Goals

- Running project-specific tests in the MVP.
- Editing the original uploaded file or a local project directly.
- Claiming cross-platform compatibility from one sandbox environment.
- Treating a successful dependency install or import check as proof that the user's application works.
- A generic AI chat interface, multiple programming languages, a full VS Code extension, multi-agent orchestration, or a second full local verification engine.

## Open Questions

No product-defining questions remain for this PRD. The technical design for future project-code testing is deferred to `4-spec` and is not part of the MVP.


