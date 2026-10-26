import { useEffect, useState } from "react";
import { previewRepairRequest, requestRepairProposal, resolveRepairProposal } from "../api/analysis";
import type { AnalysisResponse, RepairRequestPreview } from "../types/analysis";
import { ApprovalDiff } from "./ApprovalDiff";
import { VerificationStatus } from "./VerificationStatus";

export function RepairProposal({ analysis }: { analysis: AnalysisResponse }) {
  const [current, setCurrent] = useState(analysis);
  const [preview, setPreview] = useState<RepairRequestPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const proposal = current.repair_proposal;
  const canRequest = ["confirmed_conflict", "potential_issues"].includes(analysis.compatibility_report?.status ?? "")
    && analysis.resolver?.execution_mode === "real";

  useEffect(() => { setCurrent(analysis); }, [analysis.analysis_id, analysis.repair_proposal]);

  async function run(action: () => Promise<void>) {
    setBusy(true); setError(null);
    try { await action(); } catch (e) { setError(e instanceof Error ? e.message : "The request failed."); }
    finally { setBusy(false); }
  }

  return <section className="repair-proposal" aria-labelledby="repair-title">
    <div className="repair-heading"><div><p className="eyebrow">EVIDENCE-BOUNDED SUGGESTION</p><h3 id="repair-title">Repair proposal</h3></div>
      {proposal?.status === "awaiting_resolution" && <span className="status-pill status-pill--muted">PROPOSAL · RESOLUTION PENDING</span>}
      {proposal?.status === "ready" && <span className="status-pill status-pill--warn">RESOLVER ACCEPTED · NOT FULLY VERIFIED</span>}</div>
    <p className="repair-note">Nemotron selects only from the supplied PyPI candidates. Bugsy’s deterministic report remains the source of truth. Resolution requires separate approval; installation, imports, and project tests remain unverified.</p>
    {!proposal && !preview && <button className="secondary-button" type="button" disabled={!canRequest || busy} onClick={() => void run(async () => setPreview(await previewRepairRequest(analysis.analysis_id)))}>{busy ? "Preparing request…" : "Prepare repair request"}</button>}
    {!canRequest && !proposal && <p className="muted">A proposal requires real unresolved resolver evidence. SIMULATED results cannot create proposals.</p>}
    {preview && !proposal && <div className="repair-preview" aria-label="Token Factory request preview">
      <p>Review the exact sanitized request before sending it. No model call has happened yet.</p>
      <p>Endpoint: <code>{preview.base_url}</code> · model: <code>{preview.model_id}</code> · temperature: {preview.temperature} · max tokens: {preview.max_tokens} · timeout: {preview.timeout_seconds}s</p>
      <p>The backend supplies its credential in the authorization header; the secret is omitted from this preview and is never sent to the browser.</p>
      <details open><summary>System instruction</summary><pre>{preview.system_prompt}</pre></details>
      <details open><summary>Evidence JSON</summary><pre>{preview.evidence_json}</pre></details>
      <p>Preview SHA-256: <code>{preview.preview_sha256}</code></p>
      <button className="secondary-button" type="button" disabled={busy} onClick={() => void run(async () => {
        setCurrent(await requestRepairProposal(analysis.analysis_id, preview.preview_sha256));
      })}>Approve and send this request</button>
    </div>}
    {proposal?.status === "awaiting_resolution" && <div>
      <p className="repair-summary">{proposal.summary}</p>
      {proposal.candidate_evidence?.map(c => <article className="repair-change" key={c.candidate_id}>
        <strong>{c.package} {c.version}</strong><p>Requires-Python: <code>{c.requires_python ?? "not declared"}</code></p>
        <p>Requires-Dist: {c.requires_dist.length ? c.requires_dist.join(", ") : "none listed"}{c.yanked ? " · yanked release" : ""}</p>
        <a href={c.source_url} target="_blank" rel="noreferrer">PyPI release metadata</a>
      </article>)}
      {proposal.diff && <details className="repair-diff" open><summary>Review proposed requirements.txt diff</summary><pre>{proposal.diff}</pre></details>}
      <p>Diff SHA-256: <code>{proposal.diff_sha256}</code></p>
      <p>Review the exact fixed Sandbox operation and requirements file bytes. Bugsy installs only the pinned uv resolver tool in the Sandbox, then runs dependency resolution once. Submitted packages are not installed and imports are not run.</p>
      <details className="repair-diff" open><summary>Exact ConTree operation preview</summary><pre>{JSON.stringify(proposal.resolver_operation, null, 2)}</pre></details>
      <button className="secondary-button" type="button" disabled={busy} onClick={() => void run(async () => {
        if (!proposal.proposal_id || !proposal.diff_sha256) throw new Error("Proposal approval data is missing.");
        setCurrent(await resolveRepairProposal(current.analysis_id, proposal.proposal_id, proposal.diff_sha256));
      })}>Approve candidate and run one resolver check</button>
    </div>}
    {proposal?.status === "unavailable" && <div className="repair-unavailable" role="status">
      {proposal.summary && <p>{proposal.summary}</p>}
      <p>Proposal could not be completed ({proposal.error_code ?? "unknown error"}).</p>
    </div>}
    {proposal?.status === "ready" && <>
      <p className="repair-summary">{proposal.summary}</p>
      {proposal.resolver_evidence && <p className="repair-environment">Resolver evidence: {proposal.resolver_evidence.resolver} {proposal.resolver_evidence.resolver_version} · exit {proposal.resolver_evidence.exit_code} · {proposal.resolver_evidence.sandbox_os} · Python {proposal.resolver_evidence.sandbox_python}</p>}
      {proposal.diff && <details className="repair-diff" open><summary>Review requirements.txt diff</summary><pre>{proposal.diff}</pre></details>}
      <p className="repair-note">Resolver success applies to the displayed Sandbox environment only. Installation, imports, and project tests have not run.</p>
      <VerificationStatus analysis={current} proposal={proposal} /><ApprovalDiff analysis={current} proposal={proposal} />
    </>}
    {error && <p className="repair-unavailable" role="alert">{error}</p>}
  </section>;
}
