import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { VerificationStatus } from "./VerificationStatus";
import type { AnalysisResponse, ImportOperationPreview, ImportStageResult, InstallationOperationPreview, InstallationStageResult, RepairProposal } from "../types/analysis";

const api = vi.hoisted(() => ({
  fetchInstallationAttempts: vi.fn(), fetchImportAttempts: vi.fn(), previewInstallationAttempt: vi.fn(),
  previewImportAttempt: vi.fn(), startFreshInstallationAttempt: vi.fn(), startImportAttempt: vi.fn(),
  fetchVerificationAttempt: vi.fn(),
}));
vi.mock("../api/analysis", () => api);
afterEach(() => { cleanup(); vi.clearAllMocks(); vi.unstubAllGlobals(); });

const proposal: RepairProposal = {
  status: "ready", proposal_id: "proposal-1", summary: "proposal", original_sha256: "a".repeat(64),
  diff_sha256: "b".repeat(64), proposed_requirements: "requests==2.34.2\n", diff: "+requests==2.34.2\n",
  changes: [], resolver_evidence: { status: "accepted", resolver: "uv", resolver_version: "0.12.19", exit_code: 0, python_target: "3.12", sandbox_os: "Linux", sandbox_python: "3.12.13", failure_category: null }, verified: false, error_code: null,
};
const analysis: AnalysisResponse = {
  analysis_id: "analysis-1", status: "complete", source_id: "source-1", python_target: "3.12",
  requirements: [{ line: 1, original: "requests==2.31.0", name: "requests", specifier: "==2.31.0", extras: [], marker: null }],
  metadata: [], errors: [], poll_url: null, resolver: null,
};
const installation = (overrides: Partial<InstallationStageResult> = {}): InstallationStageResult => ({
  stage_id: "install-1", analysis_id: "analysis-1", proposal_id: "proposal-1", diff_sha256: "b".repeat(64),
  stage: "installation", status: "passed", execution_mode: "real", approval_state: "consumed",
  environment: { sandbox_provider: "contree", os: "Linux", python_version: "3.12.13", requested_python: "3.12", target_matches_sandbox_python: true },
  uv_version: "0.12.19", exit_code: 0, stdout: "installed", stderr: "", failure_category: null,
  failure_phase: null, failure_detail: null, artifact_available: true, limitation: null,
  created_at: "2026-10-19T00:00:00Z", updated_at: "2026-10-19T00:00:01Z", ...overrides,
});
const importResult: ImportStageResult = {
  ...installation({ stage_id: "import-1" }), stage: "imports", stdout: "requests imported",
  failure_category: null,
};
const installPreview: InstallationOperationPreview = {
  analysis_id: "analysis-1", proposal_id: "proposal-1", diff_sha256: "b".repeat(64), stage: "installation",
  requirements_sha256: "c".repeat(64), requirements_bytes_base64: btoa("requests==2.34.2\n"), requirements_byte_length: 17,
  target_python: "3.12", sandbox_image: "python:3.12-slim", resolver_tool: "uv", resolver_version: "0.12.19",
  command: "python", args: ["-c", "fixed installation script"], env: { BUGSY_PYTHON_TARGET: "3.12" },
  cwd: "/tmp", input_path: "/tmp/requirements.txt", timeout_seconds: 180, output_limit_bytes: 24000,
  retain_image: true, image_disposable: false, retention_limitation: "Cost and lifetime are unknown.",
  sandbox_enabled: true, operation_count: 1, automatic_retries: false,
};
const importsPreview: ImportOperationPreview = {
  analysis_id: "analysis-1", proposal_id: "proposal-1", diff_sha256: "b".repeat(64), stage: "imports",
  installation_stage_id: "install-1", sandbox_image_reference: "image-uuid",
  target_python: "3.12", sandbox_os: "Linux", sandbox_python: "3.12.13",
  import_mappings: [{ package: "requests", module: "requests" }],
  command: "python", args: ["-c", "fixed import script"], env: { BUGSY_IMPORT_MODULES: "[]" },
  cwd: "/tmp", timeout_seconds: 180, output_limit_bytes: 24000, image_disposable: true,
  operation_count: 1, automatic_retries: false, limitation: "Import-only.",
};

describe("VerificationStatus independent persisted stages", () => {
  it("keeps a historical unknown installation visible when a fresh attempt is approved", async () => {
    const historical = installation({ stage_id: "historical-unknown", status: "unknown", artifact_available: false, exit_code: null, stdout: "", failure_category: "unknown_failure", failure_phase: "operation_wait", failure_detail: "Transport result unavailable.", limitation: "Outcome unknown." });
    const fresh = installation({ stage_id: "fresh-install" });
    api.fetchInstallationAttempts.mockResolvedValueOnce([historical]).mockResolvedValue([historical, fresh]);
    api.fetchImportAttempts.mockResolvedValue([]);
    api.previewInstallationAttempt.mockResolvedValue(installPreview);
    api.previewImportAttempt.mockRejectedValue(new Error("A passed installation with a retained image is required."));
    api.startFreshInstallationAttempt.mockResolvedValue({ ...fresh, status: "running" });
    api.fetchVerificationAttempt.mockResolvedValue(fresh);
    vi.stubGlobal("crypto", { randomUUID: () => "fresh-request-id" });
    render(<VerificationStatus analysis={analysis} proposal={proposal} />);
    expect(await screen.findByText("Proposed requirements: dependency resolution")).toBeInTheDocument();
    expect((await screen.findAllByText("CONTREE RESULT UNAVAILABLE")).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/historical-unknown · unknown/)).toBeInTheDocument();
    const checkbox = await screen.findByRole("checkbox", { name: /I approve one fresh installation/ });
    fireEvent.click(checkbox);
    fireEvent.click(screen.getByRole("button", { name: "Approve and run installation" }));
    await waitFor(() => expect(api.startFreshInstallationAttempt).toHaveBeenCalledOnce());
    expect(api.startFreshInstallationAttempt).toHaveBeenCalledWith("analysis-1", "proposal-1", "b".repeat(64), "fresh-request-id");
    expect(await screen.findByText(/fresh-install · passed/)).toBeInTheDocument();
    expect(screen.getByText(/historical-unknown · unknown/)).toBeInTheDocument();
    expect(screen.getByText("Not run — project code not provided.")).toBeInTheDocument();
  });

  it("requires its own explicit import approval and displays the persisted result", async () => {
    api.fetchInstallationAttempts.mockResolvedValue([installation()]);
    api.fetchImportAttempts.mockResolvedValueOnce([]).mockResolvedValue([importResult]);
    api.previewInstallationAttempt.mockResolvedValue(installPreview);
    api.previewImportAttempt.mockResolvedValue(importsPreview);
    api.startImportAttempt.mockResolvedValue({ ...importResult, status: "running" });
    api.fetchVerificationAttempt.mockResolvedValue(importResult);
    vi.stubGlobal("crypto", { randomUUID: () => "imports-request-id" });
    render(<VerificationStatus analysis={analysis} proposal={proposal} />);
    const button = await screen.findByRole("button", { name: "Approve and run import checks" });
    expect(button).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox", { name: /I separately approve one import-only check/ }));
    fireEvent.click(button);
    await waitFor(() => expect(api.startImportAttempt).toHaveBeenCalledOnce());
    expect(api.startImportAttempt).toHaveBeenCalledWith("analysis-1", "proposal-1", "b".repeat(64), "imports-request-id");
    expect(await screen.findByText(/import-1 · passed/)).toBeInTheDocument();
  });

  it("fails closed if persisted history cannot be loaded and keeps project tests not run", async () => {
    api.fetchInstallationAttempts.mockRejectedValue(new Error("stage lookup unavailable"));
    api.fetchImportAttempts.mockResolvedValue([]);
    api.previewInstallationAttempt.mockResolvedValue(installPreview);
    render(<VerificationStatus analysis={analysis} proposal={proposal} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("stage lookup unavailable");
    expect(screen.getByText("Not run — project code not provided.")).toBeInTheDocument();
    expect(api.startFreshInstallationAttempt).not.toHaveBeenCalled();
    expect(api.startImportAttempt).not.toHaveBeenCalled();
  });
});
