import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { RepairProposal } from "./RepairProposal";
import type { AnalysisResponse, RepairRequestPreview } from "../types/analysis";

const api = vi.hoisted(() => ({ previewRepairRequest: vi.fn(), requestRepairProposal: vi.fn(), resolveRepairProposal: vi.fn() }));
vi.mock("../api/analysis", () => api);
afterEach(() => { cleanup(); vi.clearAllMocks(); });

const preview: RepairRequestPreview = { preview_id: "preview1", preview_sha256: "c".repeat(64), model_id: "nvidia/test", base_url: "https://tokenfactory.example/v1/", system_prompt: "Select only supplied IDs", evidence_json: '{"candidates":[{"candidate_id":"C001"}]}', temperature: 0.1, max_tokens: 2048, timeout_seconds: 25 };
function analysis(simulated = false): AnalysisResponse {
  return { analysis_id: "analysis1", status: "complete", source_id: "source1", python_target: "3.12", requirements: [], metadata: [], errors: [], poll_url: null,
    resolver: { status: "unresolved", execution_mode: simulated ? "simulated" : "real", resolver: "uv", resolver_version: "0.12.19", python_target: "3.12", sandbox_os: "Linux", sandbox_python: "3.12.13", target_matches_sandbox_python: true, exit_code: 1, stdout: simulated ? "SIMULATED" : "No solution found when resolving dependencies", stderr: "", resolved_requirements: null, limitation: null },
    compatibility_report: { status: simulated ? "simulated" : "confirmed_conflict", summary: "Conflict", findings: [] } };
}

describe("RepairProposal", () => {
  it("shows exact model request and waits for separate approval before sending", async () => {
    const initial = analysis();
    api.previewRepairRequest.mockResolvedValue(preview);
    api.requestRepairProposal.mockResolvedValue({ ...initial, repair_proposal: { status: "awaiting_resolution", proposal_id: "proposal1", summary: "Candidate selected; resolver not run.", original_sha256: "a".repeat(64), diff_sha256: "b".repeat(64), proposed_requirements: "demo==2\n", diff: "-demo==1\n+demo==2\n", changes: [], resolver_evidence: { status: "not_run", python_target: "3.12", resolver: null, resolver_version: null, exit_code: null, sandbox_os: null, sandbox_python: null, failure_category: null }, verified: false, error_code: null } });
    render(<RepairProposal analysis={initial} />);
    fireEvent.click(screen.getByRole("button", { name: "Prepare repair request" }));
    expect(await screen.findByText("Select only supplied IDs")).toBeInTheDocument();
    expect(api.requestRepairProposal).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Approve and send this request" }));
    expect(await screen.findByText(/Candidate selected; resolver not run/)).toBeInTheDocument();
    expect(screen.getByText("PROPOSAL · RESOLUTION PENDING")).toBeInTheDocument();
    expect(screen.getByText(/Diff SHA-256/)).toBeInTheDocument();
    expect(api.requestRepairProposal).toHaveBeenCalledWith("analysis1", "c".repeat(64));
    expect(api.resolveRepairProposal).not.toHaveBeenCalled();
  });

  it("does not offer proposals from visibly simulated resolver evidence", () => {
    render(<RepairProposal analysis={analysis(true)} />);
    expect(screen.getByRole("button", { name: "Prepare repair request" })).toBeDisabled();
    expect(screen.getByText(/SIMULATED results cannot create proposals/)).toBeInTheDocument();
  });

  it("labels an interrupted resolver result unknown and offers no retry", () => {
    const initial = analysis();
    initial.repair_proposal = {
      status: "unavailable", proposal_id: "proposal1", summary: "Resolver outcome unknown/incomplete: the backend process ended before saving a result. The resolver approval remains consumed; no automatic retry will occur.",
      original_sha256: "a".repeat(64), diff_sha256: "b".repeat(64), proposed_requirements: "demo==2\n", diff: null,
      changes: [], resolver_evidence: { status: "unknown", python_target: "3.12", resolver: "uv", resolver_version: "0.12.19", exit_code: null, sandbox_os: null, sandbox_python: null, failure_category: "unknown_failure" },
      verified: false, error_code: "RESOLVER_OUTCOME_UNKNOWN",
    };
    render(<RepairProposal analysis={initial} />);
    expect(screen.getByText(/Resolver outcome unknown\/incomplete/)).toBeInTheDocument();
    expect(screen.getByText(/approval remains consumed/)).toBeInTheDocument();
    expect(screen.getByText(/no automatic retry will occur/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /resolver check/i })).not.toBeInTheDocument();
    expect(api.resolveRepairProposal).not.toHaveBeenCalled();
  });
});
