import type { AnalysisResponse, MetadataResult } from "../types/analysis";

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

      <div className="report-section">
        <h3>Package metadata <span>{analysis.metadata.length}</span></h3>
        {analysis.metadata.map((item) => <MetadataRow key={item.package} item={item} />)}
        {analysis.metadata.length === 0 && <p className="muted">No metadata was returned.</p>}
      </div>

      <div className="report-section resolver-section">
        <h3>Dependency resolution <span className={`status-pill status-pill--${resolver?.status === "resolved" ? "ok" : resolver?.status === "not_run" ? "muted" : "warn"}`}>{resolver?.status === "resolved" ? "Resolved" : resolver?.status === "unresolved" ? "Unresolved" : resolver?.status === "error" ? "Error" : "Not run"}</span></h3>
        <p>Requested Python target: <strong>{analysis.python_target}</strong></p>
        {resolver?.execution_mode === "real" && <p>Actual Sandbox: <strong>{resolver.sandbox_os || "OS not reported"}</strong> · Python <strong>{resolver.sandbox_python || "version not reported"}</strong></p>}
        {resolver?.resolver && <p>Resolver: <strong>{resolver.resolver} {resolver.resolver_version || "version not reported"}</strong>{resolver.exit_code !== null && <> · exit code <strong>{resolver.exit_code}</strong></>}</p>}
        {resolver?.execution_mode === "not_run" && <p className="muted">ConTree resolution did not run. No compatibility result is available.</p>}
        {resolver?.target_matches_sandbox_python === false && <p className="warning-box">The requested Python target differs from the Sandbox Python. This run does not verify execution on the requested target.</p>}
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
