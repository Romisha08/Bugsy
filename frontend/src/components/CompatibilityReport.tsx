import type { AnalysisResponse, MetadataResult } from "../types/analysis";
import { RepairProposal } from "./RepairProposal";

function MetadataRow({ item }: { item: MetadataResult }) {
  const label = item.status === "available" ? "PyPI metadata found" : item.status === "unavailable" ? "Package unavailable on PyPI" : "Metadata request failed";
  return (
    <article className="metadata-row">
      <div className="metadata-title"><strong>{item.package}</strong><span className={`status-pill status-pill--${item.status === "available" ? "ok" : "warn"}`}>{label}</span></div>
      <p>{item.error_message ?? (item.release
        ? `Release ${item.release} · Requires-Python: ${item.requires_python || "not specified"}`
        : `Latest listed release: ${item.latest_version || "unknown"} · Requires-Python: ${item.requires_python || "not specified"}`)}</p>
      {item.requires_dist.length > 0 && <details><summary>Release dependency metadata ({item.requires_dist.length})</summary><ul>{item.requires_dist.slice(0, 20).map((dependency) => <li key={dependency}><code>{dependency}</code></li>)}</ul></details>}
      {item.source_url && <a href={item.source_url} target="_blank" rel="noreferrer">Source: PyPI JSON metadata</a>}
    </article>
  );
}

export function CompatibilityReport({ analysis }: { analysis: AnalysisResponse }) {
  const resolver = analysis.resolver;
  const simulated = resolver?.execution_mode === "simulated";
  return (
    <section className="result-card report-card" aria-labelledby="report-title">
      <div className="result-heading">
        <div><p className="eyebrow">DETERMINISTIC CHECKS</p><h2 id="report-title">Compatibility analysis</h2></div>
        {simulated && <span className="status-pill status-pill--simulated">SIMULATED</span>}
      </div>
      <p className="supporting-copy">PyPI package facts and resolver results are shown separately. This milestone does not install packages or run imports.</p>

      {analysis.compatibility_report && <section className="report-section findings-section" aria-label="Compatibility findings">
        <h3>Evidence summary <span className={`finding-status finding-status--${analysis.compatibility_report.status}`}>{analysis.compatibility_report.status.replaceAll("_", " ")}</span></h3>
        <p className="finding-summary">{analysis.compatibility_report.summary}</p>
        {analysis.compatibility_report.findings.map((finding, index) => <article className={`finding-card finding-card--${finding.classification}`} key={`${finding.code}-${index}`}>
          <div className="finding-title"><strong>{finding.title}</strong><span className={`status-pill status-pill--${finding.classification === "confirmed_conflict" ? "warn" : finding.classification === "resolved" ? "ok" : "muted"}`}>{finding.classification.replaceAll("_", " ")}</span></div>
          <p>{finding.explanation}</p>
          {finding.packages.length > 0 && <p className="finding-packages">Packages: {finding.packages.join(", ")}</p>}
          {finding.evidence.map((evidence, evidenceIndex) => <div className="evidence-item" key={`${evidence.label}-${evidenceIndex}`}>
            <span>{evidence.label}{evidence.package ? ` · ${evidence.package}` : ""}</span>
            <code>{evidence.value}</code>
            {evidence.source_url && <a href={evidence.source_url} target="_blank" rel="noreferrer">View source</a>}
          </div>)}
        </article>)}
      </section>}

      {analysis.explanation && <section className="explanation-panel" aria-label="AI explanation">
        <div className="explanation-heading"><h3>AI explanation <span>SUPPORTING CONTEXT</span></h3>
          <span className={`status-pill status-pill--${analysis.explanation.status === "available" ? "ok" : "muted"}`}>{analysis.explanation.status === "available" ? "Available" : analysis.explanation.status === "not_requested" ? "Not requested" : "Unavailable"}</span>
        </div>
        {analysis.explanation.status === "available" && <>
          <p>{analysis.explanation.text}</p>
          {analysis.explanation.evidence_refs.length > 0 && <small>References deterministic evidence: {analysis.explanation.evidence_refs.join(", ")}</small>}
          <small>Deterministic findings above remain Bugsy’s compatibility result.</small>
        </>}
        {analysis.explanation.status === "unavailable" && <p className="explanation-unavailable">Nemotron explanation is unavailable{analysis.explanation.error_code ? ` (${analysis.explanation.error_code})` : ""}. The deterministic report above is still available and remains the source of truth.</p>}
        {analysis.explanation.status === "not_requested" && <p className="explanation-unavailable">AI explanation was skipped because the resolver result is simulated. No compatibility claim is based on simulated output.</p>}
      </section>}

      {analysis.compatibility_report && <RepairProposal analysis={analysis} />}

      <div className="report-section">
        <h3>Package metadata <span>{analysis.metadata.length}</span></h3>
        {analysis.metadata.map((item) => <MetadataRow key={item.package} item={item} />)}
        {analysis.metadata.length === 0 && <p className="muted">No metadata was returned.</p>}
      </div>

      <div className="report-section resolver-section">
        <h3>Original requirements: dependency resolution <span className={`status-pill status-pill--${resolver?.status === "resolved" ? "ok" : resolver?.status === "not_run" ? "muted" : "warn"}`}>{resolver?.status === "resolved" ? "Resolved" : resolver?.status === "unresolved" ? "Unresolved" : resolver?.status === "error" ? "Error" : "Not run"}</span></h3>
        <p>Requested Python target: <strong>{analysis.python_target}</strong></p>
        {resolver?.execution_mode === "real" && <p>Actual Sandbox: <strong>{resolver.sandbox_os || "OS not reported"}</strong> · Python <strong>{resolver.sandbox_python || "version not reported"}</strong></p>}
        {resolver?.resolver && <p>Resolver: <strong>{resolver.resolver} {resolver.resolver_version || "version not reported"}</strong>{resolver.exit_code !== null && <> · exit code <strong>{resolver.exit_code}</strong></>}</p>}
        {resolver?.execution_mode === "not_run" && <p className="muted">ConTree resolution did not run. No compatibility result is available.</p>}
        {resolver?.target_matches_sandbox_python === false && <p className="warning-box">The requested Python target differs from the Sandbox Python. This run does not verify execution on the requested target.</p>}
        {(resolver?.failure_phase || resolver?.failure_category || resolver?.transport_error_type || resolver?.transport_cause_type || resolver?.transport_os_error_code != null) && <div className="resolver-diagnostics" aria-label="Resolver diagnostics">
          {resolver?.failure_phase && <p>Failure phase: <strong>{resolver.failure_phase.replaceAll("_", " ")}</strong></p>}
          {resolver?.failure_category && <p>Failure category: <strong>{resolver.failure_category.replaceAll("_", " ")}</strong></p>}
          {(resolver?.transport_error_type || resolver?.transport_cause_type) && <p>Transport error type: <strong>{resolver?.transport_error_type || resolver?.transport_cause_type}</strong></p>}
          {resolver?.transport_os_error_code != null && <p>OS error code: <strong>{resolver.transport_os_error_code}</strong></p>}
        </div>}
        {resolver?.limitation && <p className="limitation">{resolver.limitation}</p>}
        {resolver?.stdout && <details><summary>Resolver output</summary><pre>{resolver.stdout}</pre></details>}
        {resolver?.stderr && <details open><summary>Resolver error output</summary><pre>{resolver.stderr}</pre></details>}
        {resolver?.resolved_requirements && <details><summary>Resolved package set</summary><pre>{resolver.resolved_requirements}</pre></details>}
      </div>
      {analysis.errors.map((error) => <p className="warning-box" key={error}>{error}</p>)}
      <p className="scope-note">A resolver result does not mean packages were installed or imported. It does not prove that an application works or guarantee compatibility on another Python version or operating system.</p>
    </section>
  );
}
