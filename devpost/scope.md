---
doc: scope
status: approved
---

# Bugsy

Bugsy is a Python dependency repair assistant for developers, students, and hackathon participants. Users upload or paste a `requirements.txt`, choose a Python version, review the parsed requirements, get evidence-backed repair proposals, and verify proposed requirements in an isolated Nebius sandbox.

## The Unique Kernel
Bugsy ties a repair recommendation to compatibility evidence and an actual sandbox check. It reports dependency resolution, installation, and import checks separately, and never presents successful dependency checks as proof that the user's application works.

## Who It's For
Developers, students learning Python, and hackathon participants who encounter dependency conflicts or a broken Python environment. They need to understand why their requested packages do not fit together and what change is likely to work.

## The Core Loop
The user uploads or pastes a `requirements.txt`, selects a Python version, and reviews the read-only parsed requirements before analysis. Bugsy analyzes package metadata, dependency constraints, and OS-specific markers where supported with a real resolver; it explains conflicts and recommends compatible package or Python versions with supporting evidence. NVIDIA Nemotron through Nebius Token Factory helps with diagnosis and repair planning. The user requests verification in Nebius, reviews separate dependency check results and a proposed file diff, approves the proposal, and downloads a revised `requirements.txt`. Bugsy shows the actual sandbox operating system and Python version used for verification; it does not claim cross-platform compatibility. Project-specific tests are not run in the MVP.

## Inspiration & Identity
No visual references were provided. The requested product direction is a professional dark developer interface with restrained, tasteful color and subtle bug-inspired details.

## Why This Matters to the Learner
The learner regularly faces Python package installation failures and wants first to make Bugsy work properly. They also want to learn about Nebius, the hackathon, and sandbox execution.

## What "Working" Looks Like
A user uploads or pastes a `requirements.txt`, chooses a Python version, and sees a read-only preview of parsed package names and version constraints. If the file contains malformed or unsupported dependency lines Bugsy cannot reliably interpret, it pauses full compatibility analysis and shows affected line numbers, original lines, explanations, and a request to upload or paste a corrected file. The user can replace the source and parse again. Valid supported requirement syntax and environment markers are not incorrectly flagged. Successful parsing is clearly distinct from compatibility analysis.

Bugsy explains conflicts with compatibility evidence, proposes changes, and verifies the proposed requirements in the Nebius sandbox. The report separately shows dependency resolution, package installation, import smoke test, and project test status, including captured errors and skipped checks. Import smoke tests run only when the distribution-to-import mapping is reliable. Project-specific tests are not run in the MVP; the report states “not run—project code not provided.” A successful install or import alone does not mark the repair fully verified or prove the user's application works.

The report shows the actual sandbox operating system and Python version. It explains that results apply only to that environment and do not guarantee compatibility on the user's local machine or other operating systems. OS-specific requirement markers are considered where supported; cross-platform compatibility is not claimed without testing it.

Before dependency checks are added, the Nebius SDK connection is confirmed with a harmless Python command in the sandbox. Dependency resolution, installation, and import checks run in the sandbox; model-generated commands are never run directly on the host. Sandbox integration is controlled by a configuration flag so development can continue when the service is unavailable. The user sees a clear summary and diff of proposed changes and explicitly approves before downloading a revised `requirements.txt`. The original file is preserved and never modified directly.

## In-Scope Features
- Upload or paste `requirements.txt` only; no separate manual package-entry feature. Keep the parser modular so another format can be added later.
- Select a Python version for the requested analysis and verification.
- Preview parsed package names and version constraints read-only; flag malformed or unsupported lines with actionable feedback; block full analysis until a corrected file is parsed; allow replacing the source file and parsing again.
- Use package metadata and a real resolver to detect and explain conflicts and provide compatibility evidence for recommendations.
- Account for supported OS-specific dependency markers; do not offer an OS target selector.
- Use NVIDIA Nemotron through Nebius Token Factory for diagnosis and repair planning.
- Verify dependency resolution, installation, and import checks in the Nebius sandbox and display the actual sandbox OS and Python version.
- Report dependency resolution, package installation, import smoke test, and project test status separately. In the MVP, project tests are always reported as not run because project code is not provided.
- Show a clear proposed-change summary and diff, require approval, then offer the revised requirements file for download while preserving the original.
- Prioritize a reliable, demonstrable end-to-end workflow over feature breadth.

## Later
- Add `pyproject.toml` support after the `requirements.txt` workflow is reliable.
- Add optional project-code and test uploads and project-specific testing after the MVP; keep this capability possible in a future product design, with technical architecture decisions deferred to `4-spec`.
- Consider separate manual package entry as a possible future enhancement.
- Consider other improvements after the initial workflow is proven.

## Out-of-Scope Features
- Separate manual package entry: the MVP accepts uploaded or pasted `requirements.txt` files.
- Project-code and test uploads and project-specific test execution: the MVP verifies dependency resolution, installation, and imports only.
- Multi-agent orchestration: adds complexity without being needed to prove the repair workflow.
- Multiple programming languages: the proof of concept is focused on Python dependency problems.
- A full VS Code extension: a separate interface is not needed to demonstrate the core workflow.
- A second full local verification engine: Nebius is the primary verification environment; duplicating it locally expands implementation and maintenance work.
- Directly modifying the user's uploaded file or local project: Bugsy preserves the source and provides a revised file only after proposal approval.
- OS target selection and cross-platform guarantees: the MVP verifies in the actual Nebius sandbox environment and reports its OS and Python version.
- Unnecessary paid external services: keep dependencies and services to what the planned workflow needs.

## Safety Requirements
- Run dependency resolution, installation, and import checks in the Nebius sandbox. Never run model-generated commands directly on the host machine.
- Gate sandbox integration with a configuration flag so development can continue when the service is unavailable. Make unavailable or skipped verification visible and do not claim the repair is verified.
- First confirm the Nebius SDK can connect and execute a harmless Python command; only then add dependency installation and resolution checks.
- Do not claim the whole repair is fully verified from installation or import success alone. Show every check and its errors, including skipped and not-run checks. Do not imply successful dependency checks prove the user's application works.
- Preserve the original requirements file. Do not offer the revised file until the repair proposal is ready and the user approves its change summary and diff. Clearly distinguish proposed changes from successfully verified changes.
- Show the actual sandbox OS and Python version and limit compatibility statements to the tested environment. Account for OS markers only where supported.
- Do not run project-specific tests in the MVP; show the status “not run—project code not provided.”

## Acceptance Criteria
- A user can upload or paste `requirements.txt` and choose a Python version. The MVP has no separate manual package entry and no OS target selector.
- Before analysis, Bugsy displays a read-only preview of parsed package names and version constraints. Malformed or unsupported lines that cannot be reliably interpreted show line numbers, original text, explanations, and a request to provide a corrected file; full compatibility analysis pauses until parsing succeeds. The user can replace the source and parse again. Supported syntax and valid environment markers are not incorrectly flagged. Parse success is distinct from compatibility success.
- Bugsy uses package metadata and a real resolver to identify conflicts and support version recommendations with compatibility evidence, including supported OS markers.
- Bugsy uses NVIDIA Nemotron through Nebius Token Factory for diagnosis and repair planning.
- The Nebius connection is first demonstrated by a harmless Python command. Proposed requirements are then checked in the sandbox.
- The report identifies the actual sandbox OS and Python version and states that results do not guarantee compatibility on other environments.
- The report gives separate statuses for dependency resolution, package installation, import smoke test, and project tests, with captured errors and clear pass, fail, skipped, or not-run states. Project tests are “not run—project code not provided” in the MVP.
- Import smoke tests are attempted only when a distribution-to-import mapping can be identified reliably; otherwise the import check is skipped.
- A successful installation or import check alone never produces an overall “fully verified” result and never implies that the user's application works; any skipped or unavailable check is visible and limits the verification claim.
- Bugsy shows proposed changes in a clear summary and diff, distinguishes proposal status from verification status, and enables revised-file download only after the proposal is ready and the user approves it. The original input remains unchanged.
- When the sandbox service is unavailable or disabled, development can continue and the report makes clear that sandbox verification did not happen.
