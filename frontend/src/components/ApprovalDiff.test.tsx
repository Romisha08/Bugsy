import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ApprovalDiff } from "./ApprovalDiff";
import type { AnalysisResponse, RepairProposal } from "../types/analysis";

const api = vi.hoisted(() => ({ approveProposalDownload: vi.fn(), downloadProposal: vi.fn() }));
vi.mock("../api/analysis", () => api);

afterEach(() => { cleanup(); vi.clearAllMocks(); vi.unstubAllGlobals(); });

const analysis: AnalysisResponse = { analysis_id: "analysis-1", status: "complete", source_id: "source-1", python_target: "3.12", requirements: [], metadata: [], errors: [], poll_url: null, resolver: null };
const proposal: RepairProposal = { status: "ready", proposal_id: "proposal-1", summary: "proposal", original_sha256: "a".repeat(64), diff_sha256: "b".repeat(64), proposed_requirements: "demo==2\n", diff: "+demo==2\n", changes: [], resolver_evidence: null, verified: false, error_code: null };

describe("ApprovalDiff", () => {
  it("requires approval of the displayed diff before requesting a download", async () => {
    api.approveProposalDownload.mockResolvedValue(undefined);
    api.downloadProposal.mockResolvedValue(new Blob(["demo==2\n"], { type: "text/plain" }));
    vi.stubGlobal("URL", { createObjectURL: vi.fn(() => "blob:test"), revokeObjectURL: vi.fn() });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    render(<ApprovalDiff analysis={analysis} proposal={proposal} />);
    const button = screen.getByRole("button", { name: "Approve and download requirements.txt" });
    expect(button).toBeDisabled();
    expect(api.approveProposalDownload).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("checkbox", { name: /I approve downloading this exact diff/ }));
    expect(button).toBeEnabled();
    fireEvent.click(button);
    expect(api.approveProposalDownload).toHaveBeenCalledWith("analysis-1", "proposal-1", "b".repeat(64));
    await vi.waitFor(() => { expect(api.downloadProposal).toHaveBeenCalledWith("analysis-1"); });
    expect(click).toHaveBeenCalledOnce();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:test");
  });
});
