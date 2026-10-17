import type { ParseResponse } from "../types/requirements";

export function ParsedPreview({ parsed, onAnalyze, analyzing }: { parsed: ParseResponse; onAnalyze: () => void; analyzing: boolean }) {
  return (
    <section className="result-card" aria-labelledby="parsed-title">
      <div className="result-heading">
        <div>
          <p className="eyebrow">INPUT CHECK</p>
          <h2 id="parsed-title">Parsed requirements</h2>
        </div>
        <span className="status-pill status-pill--ok">Parsed · ready for analysis</span>
      </div>
      <p className="supporting-copy">Parsing confirms Bugsy understood the file. It does not mean the dependencies are compatible.</p>
      <div className="requirements-list" role="list" aria-label="Read-only parsed requirements">
        {parsed.requirements.map((requirement) => (
          <article className="requirement-row" role="listitem" key={`${requirement.line}-${requirement.name}`}>
            <span className="line-number">{requirement.line.toString().padStart(2, "0")}</span>
            <div className="requirement-main">
              <strong>{requirement.name}{requirement.extras.length ? `[${requirement.extras.join(",")}]` : ""}</strong>
              <code>{requirement.specifier || "any version"}</code>
              {requirement.marker && <span className="marker">marker: {requirement.marker}</span>}
            </div>
            <code className="original-line">{requirement.original}</code>
          </article>
        ))}
        {parsed.requirements.length === 0 && <p className="supporting-copy">No dependencies were found in this file.</p>}
      </div>
      <div className="input-actions preview-actions">
        <p>{parsed.requirements.length} direct package {parsed.requirements.length === 1 ? "requirement" : "requirements"} · Python target {parsed.python_version}</p>
        <button type="button" className="primary-button" disabled={analyzing || parsed.requirements.length === 0} onClick={onAnalyze}>
          {analyzing ? "Analyzing…" : "Analyze dependencies"}<span aria-hidden="true">→</span>
        </button>
      </div>
    </section>
  );
}
