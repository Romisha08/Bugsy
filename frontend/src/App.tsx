import { useEffect, useState } from "react";
import { fetchAnalysis, startAnalysis } from "./api/analysis";
import { BugsyApiError } from "./api/requirements";
import { fetchHealth } from "./api/health";
import { CompatibilityReport } from "./components/CompatibilityReport";
import { ParsedPreview } from "./components/ParsedPreview";
import { RequirementsInput } from "./components/RequirementsInput";
import type { AnalysisResponse } from "./types/analysis";
import type { ParseIssue, ParseResponse } from "./types/requirements";

type ConnectionState = "checking" | "connected" | "unavailable";

function parseFailure(error: unknown): { message: string; issues: ParseIssue[] } {
  if (error instanceof BugsyApiError && error.body) {
    return {
      message: error.message,
      issues: Array.isArray(error.body.details) ? error.body.details as ParseIssue[] : [],
    };
  }
  return { message: error instanceof Error ? error.message : "Bugsy could not parse this file. Try again.", issues: [] };
}

const wait = (milliseconds: number) => new Promise((resolve) => window.setTimeout(resolve, milliseconds));

export default function App() {
  const [connection, setConnection] = useState<ConnectionState>("checking");
  const [parsed, setParsed] = useState<ParseResponse | null>(null);
  const [issues, setIssues] = useState<ParseIssue[]>([]);
  const [parseError, setParseError] = useState<string | null>(null);
  const [parsing, setParsing] = useState(false);
  const [analysis, setAnalysis] = useState<AnalysisResponse | null>(null);
  const [analysisError, setAnalysisError] = useState<string | null>(null);
  const [analyzing, setAnalyzing] = useState(false);

  useEffect(() => {
    let active = true;
    fetchHealth()
      .then((health) => { if (active) setConnection(health.status === "ok" ? "connected" : "unavailable"); })
      .catch(() => { if (active) setConnection("unavailable"); });
    return () => { active = false; };
  }, []);

  function clearForSourceChange() {
    setParsed(null);
    setIssues([]);
    setParseError(null);
    setAnalysis(null);
    setAnalysisError(null);
  }

  function handleParseError(error: unknown) {
    const failure = parseFailure(error);
    setParsed(null);
    setIssues(failure.issues);
    setParseError(failure.message);
    setParsing(false);
  }

  async function handleAnalyze() {
    if (!parsed) return;
    setAnalyzing(true);
    setAnalysisError(null);
    try {
      const started = await startAnalysis(parsed.source_id);
      setAnalysis(started);
      let current = started;
      for (let attempt = 0; attempt < 60 && (current.status === "queued" || current.status === "running"); attempt += 1) {
        await wait(800);
        current = await fetchAnalysis(started.analysis_id);
        setAnalysis(current);
      }
      if (current.status === "queued" || current.status === "running") {
        setAnalysisError("Analysis is still running. Refreshing this page would lose the in-memory job; try checking again shortly.");
      }
    } catch (error) {
      setAnalysisError(error instanceof Error ? error.message : "Analysis could not be completed.");
    } finally {
      setAnalyzing(false);
    }
  }

  return (
    <main className="shell">
      <header className="topbar">
        <a className="brand" href="#top" aria-label="Bugsy home"><span className="brand-mark" aria-hidden="true">✳</span><span>bugsy</span><span className="brand-caption">PYTHON DEPENDENCY ASSISTANT</span></a>
        <div className={`connection connection--${connection}`} role="status" aria-live="polite">
          <span className="connection-dot" aria-hidden="true" />
          {connection === "checking" ? "Connecting to API" : connection === "connected" ? "Backend connected" : "Backend unavailable"}
        </div>
      </header>

      <section id="top" className="hero">
        <div className="hero-copy">
          <p className="eyebrow">DEPENDENCIES, WITH RECEIPTS</p>
          <h1>Make dependencies<br /><span>make sense.</span></h1>
          <p className="intro">Bring a requirements file. Bugsy checks what it understood, gathers package facts, and asks a real resolver when ConTree is enabled.</p>
        </div>
        <div className="workflow-strip" aria-label="Bugsy workflow">
          <div><span>01</span><strong>Parse</strong><small>Review the input</small></div>
          <div><span>02</span><strong>Gather evidence</strong><small>Read PyPI metadata</small></div>
          <div><span>03</span><strong>Resolve</strong><small>Isolated ConTree run</small></div>
        </div>
      </section>

      <div className="workspace">
        <div className="main-column">
          <RequirementsInput
            busy={parsing}
            onSourceChange={clearForSourceChange}
            onParsed={(response) => { setParsed(response); setIssues([]); setParseError(null); setParsing(false); }}
            onParseError={handleParseError}
          />

          {parseError && <section className="error-card" role="alert">
            <div className="result-heading"><div><p className="eyebrow">NEEDS ATTENTION</p><h2>We couldn't parse every line</h2></div><span className="status-pill status-pill--warn">Analysis paused</span></div>
            <p>{parseError}</p>
            {issues.length > 0 && <div className="issue-list">{issues.map((issue) => <article className="issue-row" key={`${issue.line}-${issue.original}`}>
              <span className="line-number">{issue.line.toString().padStart(2, "0")}</span><div><code>{issue.original || "(blank line)"}</code><p>{issue.reason}</p></div>
            </article>)}</div>}
            <p className="supporting-copy">Replace the source above and parse it again. Bugsy will not run full analysis on a partially understood file.</p>
          </section>}

          {parsed && <ParsedPreview parsed={parsed} onAnalyze={() => void handleAnalyze()} analyzing={analyzing} />}
          {analysisError && <div className="error-card" role="alert"><strong>Analysis status unavailable</strong><p>{analysisError}</p></div>}
          {analysis && (analysis.status === "queued" || analysis.status === "running") && <div className="loading-card" role="status">{analysis.status === "queued" ? "Analysis queued…" : "Reading metadata and checking resolution…"}</div>}
          {analysis && (analysis.status === "complete" || analysis.status === "analysis_incomplete") && <CompatibilityReport analysis={analysis} />}
        </div>

        <aside className="side-column">
          <section className="side-card">
            <p className="eyebrow">WHAT BUGSY CHECKS</p>
            <ol className="check-list">
              <li><span className="check-number">01</span><div><strong>Understand your file</strong><p>Every dependency line is parsed before analysis can start.</p></div></li>
              <li><span className="check-number">02</span><div><strong>Collect package facts</strong><p>Metadata comes from PyPI and includes source details.</p></div></li>
              <li><span className="check-number">03</span><div><strong>Resolve in isolation</strong><p>Real resolution runs only in ConTree when explicitly enabled.</p></div></li>
            </ol>
          </section>
          <section className="note-card"><span aria-hidden="true">✦</span><div><strong>Honest checks only</strong><p>Resolver success is not an install, import, or application test. Results apply to the reported target and environment only.</p></div></section>
          <p className="privacy-note">Project-specific tests won't run in this milestone because project code is not provided.</p>
        </aside>
      </div>
      <footer>Bugsy · requirements.txt only · Original input stays unchanged</footer>
    </main>
  );
}
