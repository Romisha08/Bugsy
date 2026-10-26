import { useCallback, useEffect, useState } from "react";
import {
  fetchImportAttempts, fetchInstallationAttempts, fetchVerificationAttempt,
  previewImportAttempt, previewInstallationAttempt, startFreshInstallationAttempt, startImportAttempt,
} from "../api/analysis";
import type {
  AnalysisResponse, ImportOperationPreview, ImportStageResult, InstallationOperationPreview,
  InstallationStageResult, RepairProposal,
} from "../types/analysis";

type CheckStatus = "passed" | "failed" | "unknown" | "incomplete" | "not_run" | "skipped" | "running";
const wait = (ms: number) => new Promise((resolve) => window.setTimeout(resolve, ms));
const decodeBase64Utf8 = (value: string) => new TextDecoder().decode(Uint8Array.from(atob(value), (char) => char.charCodeAt(0)));

function CheckRow({ title, status, children }: { title: string; status: CheckStatus; children: React.ReactNode }) {
  const style = status === "passed" ? "ok" : status === "failed" ? "warn" : "muted";
  return <article className="verification-check">
    <div className="verification-check-heading"><strong>{title}</strong><span className={"status-pill status-pill--" + style}>{status.replaceAll("_", " ")}</span></div>
    <div className="verification-evidence">{children}</div>
  </article>;
}

function resolverStatus(proposal: RepairProposal): CheckStatus {
  const status = proposal.resolver_evidence?.status;
  return status === "accepted" ? "passed" : status === "rejected" ? "failed" : status === "not_run" ? "not_run" : "unknown";
}

function AttemptEvidence({ result, title }: { result: InstallationStageResult | ImportStageResult; title: string }) {
  return <details className="verification-attempt" open={result.status === "unknown"}>
    <summary>{title} · {result.status}</summary>
    {result.stage === "installation" && result.status === "unknown" && <span className="status-pill status-pill--muted">CONTREE RESULT UNAVAILABLE</span>}
    <p>Sandbox: {result.environment.os ?? "OS not reported"} · Python {result.environment.python_version ?? "version not reported"} · requested {result.environment.requested_python}</p>
    <p>Exit status: {result.exit_code ?? "not reported"}; approval: {result.approval_state}</p>
    {result.failure_category && <p>Failure category: {result.failure_category.replaceAll("_", " ")}</p>}
    {result.failure_phase && <p>Failure phase: {result.failure_phase.replaceAll("_", " ")}</p>}
    {result.failure_detail && <p>{result.failure_detail}</p>}
    {result.limitation && <p>{result.limitation}</p>}
    {(result.stdout || result.stderr) && <details><summary>Bounded diagnostics</summary>{result.stdout && <pre>{result.stdout}</pre>}{result.stderr && <pre>{result.stderr}</pre>}</details>}
  </details>;
}

export function VerificationStatus({ analysis, proposal }: { analysis: AnalysisResponse; proposal: RepairProposal }) {
  const [installations, setInstallations] = useState<InstallationStageResult[]>([]);
  const [imports, setImports] = useState<ImportStageResult[]>([]);
  const [installPreview, setInstallPreview] = useState<InstallationOperationPreview | null>(null);
  const [importPreview, setImportPreview] = useState<ImportOperationPreview | null>(null);
  const [loading, setLoading] = useState(true);
  const [lookupError, setLookupError] = useState<string | null>(null);
  const [importPreviewError, setImportPreviewError] = useState<string | null>(null);
  const [installApproved, setInstallApproved] = useState(false);
  const [importApproved, setImportApproved] = useState(false);
  const [runningStage, setRunningStage] = useState<"installation" | "imports" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadState = useCallback(async () => {
    if (!proposal.proposal_id || !proposal.diff_sha256) {
      setLookupError("Proposal approval data is missing; verification is unavailable.");
      setLoading(false);
      return;
    }
    setLoading(true);
    setLookupError(null);
    try {
      const [installRows, importRows, installPlan] = await Promise.all([
        fetchInstallationAttempts(analysis.analysis_id, proposal.proposal_id),
        fetchImportAttempts(analysis.analysis_id, proposal.proposal_id),
        previewInstallationAttempt(analysis.analysis_id, proposal.proposal_id),
      ]);
      const validInstall = installRows.every((r) => r.proposal_id === proposal.proposal_id && r.diff_sha256 === proposal.diff_sha256 && r.stage === "installation");
      const validImport = importRows.every((r) => r.proposal_id === proposal.proposal_id && r.diff_sha256 === proposal.diff_sha256 && r.stage === "imports");
      if (!validInstall || !validImport || installPlan.proposal_id !== proposal.proposal_id || installPlan.diff_sha256 !== proposal.diff_sha256) throw new Error("Saved verification history or operation preview does not match this proposal.");
      setInstallations(installRows);
      setImports(importRows);
      setInstallPreview(installPlan);
      setInstallApproved(false);
      setImportApproved(false);
      try {
        setImportPreview(await previewImportAttempt(analysis.analysis_id, proposal.proposal_id));
        setImportPreviewError(null);
      } catch (e) {
        setImportPreview(null);
        setImportPreviewError(e instanceof Error ? e.message : "A retained successful installation is required before imports.");
      }
    } catch (e) {
      setLookupError(e instanceof Error ? e.message : "Could not load persisted verification state.");
      setInstallPreview(null);
    } finally {
      setLoading(false);
    }
  }, [analysis.analysis_id, proposal.proposal_id, proposal.diff_sha256]);

  useEffect(() => { void loadState(); }, [loadState]);

  async function poll(id: string): Promise<InstallationStageResult | ImportStageResult> {
    let result = await fetchVerificationAttempt(id);
    for (let i = 0; i < 180 && result.status === "running"; i += 1) {
      await wait(1_000);
      result = await fetchVerificationAttempt(id);
    }
    return result;
  }

  async function runInstallation() {
    if (!installApproved || !installPreview?.sandbox_enabled || loading || runningStage || lookupError || !proposal.proposal_id || !proposal.diff_sha256) return;
    setRunningStage("installation"); setError(null);
    try {
      const claimed = await startFreshInstallationAttempt(analysis.analysis_id, proposal.proposal_id, proposal.diff_sha256, crypto.randomUUID());
      setInstallations((items) => [...items, claimed]);
      const finished = await poll(claimed.stage_id);
      setInstallations((items) => items.map((item) => item.stage_id === finished.stage_id ? finished as InstallationStageResult : item));
      await loadState();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Installation result could not be confirmed. No automatic retry was made.");
      await loadState();
    } finally { setRunningStage(null); setInstallApproved(false); }
  }

  async function runImports() {
    if (!importApproved || !importPreview || loading || runningStage || lookupError || !proposal.proposal_id || !proposal.diff_sha256) return;
    setRunningStage("imports"); setError(null);
    try {
      const claimed = await startImportAttempt(analysis.analysis_id, proposal.proposal_id, proposal.diff_sha256, crypto.randomUUID());
      setImports((items) => [...items, claimed]);
      const finished = await poll(claimed.stage_id);
      setImports((items) => items.map((item) => item.stage_id === finished.stage_id ? finished as ImportStageResult : item));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Import result could not be confirmed. No automatic retry was made.");
      await loadState();
    } finally { setRunningStage(null); setImportApproved(false); }
  }

  const isRunning = installations.some((r) => r.status === "running") || imports.some((r) => r.status === "running") || runningStage !== null;
  const latestInstall = installations.at(-1);
  const latestImport = imports.at(-1);
  return <section className="verification-panel" aria-labelledby="verification-title">
    <div className="verification-heading">
      <div><p className="eyebrow">ISOLATED PACKAGE CHECKS</p><h4 id="verification-title">Verification stages</h4></div>
      {latestInstall?.status === "unknown" && <span className="status-pill status-pill--muted">CONTREE RESULT UNAVAILABLE</span>}
    </div>
    <p className="scope-note">Each stage has separate evidence and approval. Installation can evaluate dependency constraints as part of installing; it does not rerun Bugsy’s standalone resolver. Imports run only against a confirmed retained installation image. Project tests remain not run because project code was not provided.</p>
    {loading && <p role="status">Loading persisted verification history and operation details…</p>}
    {lookupError && <div className="repair-unavailable" role="alert"><p>{lookupError}</p><button className="secondary-button" type="button" onClick={() => void loadState()}>Retry status lookup</button></div>}
    {error && <p className="repair-unavailable" role="alert">{error}</p>}
    <div className="verification-checks" aria-label="Dependency verification results">
      <CheckRow title="Proposed requirements: dependency resolution" status={resolverStatus(proposal)}>
        {proposal.resolver_evidence ? <><p>Resolver status: {proposal.resolver_diagnostics?.status ?? proposal.resolver_evidence.status}; {proposal.resolver_evidence.resolver ?? "resolver not reported"} {proposal.resolver_evidence.resolver_version ?? ""} · exit {proposal.resolver_evidence.exit_code ?? "not reported"}</p><p>Target Python {proposal.resolver_evidence.python_target}; Sandbox {proposal.resolver_diagnostics?.sandbox_os ?? proposal.resolver_evidence.sandbox_os ?? "OS not reported"} · Python {proposal.resolver_diagnostics?.sandbox_python ?? proposal.resolver_evidence.sandbox_python ?? "version not reported"}</p>{proposal.resolver_evidence.failure_category && <p>Failure category: {proposal.resolver_evidence.failure_category.replaceAll("_", " ")}</p>}{proposal.resolver_diagnostics?.limitation && <p>{proposal.resolver_diagnostics.limitation}</p>}{(proposal.resolver_diagnostics?.stdout || proposal.resolver_diagnostics?.stderr) && <details><summary>Resolver diagnostics</summary>{proposal.resolver_diagnostics.stdout && <pre>{proposal.resolver_diagnostics.stdout}</pre>}{proposal.resolver_diagnostics.stderr && <pre>{proposal.resolver_diagnostics.stderr}</pre>}</details>}</> : <p>Resolver evidence was not recorded.</p>}
      </CheckRow>
      <CheckRow title="Package installation" status={latestInstall?.status ?? "not_run"}>
        {installations.length ? installations.map((item, i) => <AttemptEvidence key={item.stage_id} result={item} title={"Installation attempt " + (i + 1) + " · " + item.stage_id} />) : <p>Not run — no installation attempt has been recorded.</p>}
      </CheckRow>
      <CheckRow title="Import checks" status={latestImport?.status ?? "not_run"}>
        {imports.length ? imports.map((item, i) => <AttemptEvidence key={item.stage_id} result={item} title={"Import attempt " + (i + 1) + " · " + item.stage_id} />) : <p>{importPreview ? "Not run — a reliable import mapping is available; approve the separate import stage below." : "Not run — " + (importPreviewError ?? "no verified install image is available.")}</p>}
      </CheckRow>
      <CheckRow title="Project tests" status="not_run"><p>Not run — project code not provided.</p></CheckRow>
    </div>

    {installPreview && <section className="stage-approval" aria-label="Installation approval">
      <h5>Fresh installation operation</h5>
      <p>Proposal hash {installPreview.diff_sha256}; requirements SHA-256 {installPreview.requirements_sha256}; {installPreview.requirements_byte_length} UTF-8 bytes; target Python {installPreview.target_python}; image {installPreview.sandbox_image}; uv {installPreview.resolver_version}; timeout {installPreview.timeout_seconds}s; output cap {installPreview.output_limit_bytes} bytes.</p>
      <p>{installPreview.retain_image ? "A successful installation will retain its resulting Sandbox image for a separately approved import check." : "Image retention is disabled; a successful installation will be disposable and cannot be followed by a separate import check."} {installPreview.retention_limitation}</p>
      <details><summary>Exact requirements bytes and fixed operation</summary><pre>{decodeBase64Utf8(installPreview.requirements_bytes_base64)}</pre><pre>{installPreview.command + " " + JSON.stringify(installPreview.args) + "\n" + JSON.stringify(installPreview.env)}</pre></details>
      {!installPreview.sandbox_enabled && <p role="status">Sandbox execution is disabled. Enable it through the existing configuration before approving a live operation.</p>}
      {installPreview.sandbox_enabled && <><label className="approval-choice"><input type="checkbox" checked={installApproved} disabled={loading || isRunning || Boolean(lookupError)} onChange={(e) => setInstallApproved(e.target.checked)} />I approve one fresh installation for this exact proposal and hash.</label><button className="secondary-button" type="button" disabled={!installApproved || loading || isRunning || Boolean(lookupError)} onClick={() => void runInstallation()}>{runningStage === "installation" ? "Installing in ConTree…" : "Approve and run installation"}</button></>}
    </section>}

    {importPreview && <section className="stage-approval" aria-label="Import verification approval">
      <h5>Separate import-only operation</h5>
      <p>Uses retained image from installation attempt {importPreview.installation_stage_id}; actual Sandbox {importPreview.sandbox_os ?? "OS not reported"} · Python {importPreview.sandbox_python ?? "version not reported"}; requested Python {importPreview.target_python}; image reference {importPreview.sandbox_image_reference}; timeout {importPreview.timeout_seconds}s; output cap {importPreview.output_limit_bytes} bytes.</p>
      <p>Allowlisted imports: {importPreview.import_mappings.map((m) => m.package + " → " + m.module).join(", ")}. No resolution, installation, or project tests run.</p>
      <details><summary>Fixed import command</summary><pre>{importPreview.command + " " + JSON.stringify(importPreview.args) + "\n" + JSON.stringify(importPreview.env)}</pre></details>
      <label className="approval-choice"><input type="checkbox" checked={importApproved} disabled={loading || isRunning || Boolean(lookupError)} onChange={(e) => setImportApproved(e.target.checked)} />I separately approve one import-only check for this proposal.</label>
      <button className="secondary-button" type="button" disabled={!importApproved || loading || isRunning || Boolean(lookupError)} onClick={() => void runImports()}>{runningStage === "imports" ? "Checking imports in ConTree…" : "Approve and run import checks"}</button>
    </section>}
  </section>;
}
