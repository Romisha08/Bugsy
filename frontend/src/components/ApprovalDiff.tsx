import { useState } from "react";
import { approveProposalDownload, downloadProposal } from "../api/analysis";
import type { AnalysisResponse, RepairProposal } from "../types/analysis";

export function ApprovalDiff({ analysis, proposal }: { analysis: AnalysisResponse; proposal: RepairProposal }) {
  const [approved, setApproved] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleDownload() {
    if (!approved || !proposal.proposal_id || !proposal.diff_sha256) return;
    setBusy(true);
    setError(null);
    try {
      await approveProposalDownload(analysis.analysis_id, proposal.proposal_id, proposal.diff_sha256);
      const file = await downloadProposal(analysis.analysis_id);
      const url = URL.createObjectURL(file);
      const link = document.createElement("a");
      link.href = url;
      link.download = "requirements.txt";
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The revised requirements file could not be downloaded.");
    } finally {
      setBusy(false);
    }
  }

  return <section className="download-approval" aria-labelledby="download-approval-title">
    <h4 id="download-approval-title">Approve revised file download</h4>
    <p>The original requirements remain unchanged. This approval applies only to the diff shown above.</p>
    <label className="approval-choice"><input type="checkbox" checked={approved} onChange={(event) => setApproved(event.target.checked)} />
      I approve downloading this exact diff ({proposal.diff_sha256?.slice(0, 12) ?? "missing"}…)
    </label>
    <button className="secondary-button" type="button" disabled={!approved || busy} onClick={() => void handleDownload()}>
      {busy ? "Preparing download…" : "Approve and download requirements.txt"}
    </button>
    {error && <p className="repair-unavailable" role="alert">{error}</p>}
  </section>;
}
