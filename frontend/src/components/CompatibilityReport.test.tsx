import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { CompatibilityReport } from "./CompatibilityReport";
import type { AnalysisResponse } from "../types/analysis";

afterEach(cleanup);

function analysis(reportStatus: "confirmed_conflict" | "resolved_in_environment" | "simulated"): AnalysisResponse {
  const simulated = reportStatus === "simulated";
  return {
    analysis_id: "a1", status: "complete", source_id: "s1", python_target: "3.12", requirements: [], metadata: [], errors: [], poll_url: null,
    resolver: { status: simulated ? "unresolved" : reportStatus === "confirmed_conflict" ? "unresolved" : "resolved", execution_mode: simulated ? "simulated" : "real", resolver: "uv", resolver_version: "0.12.19", python_target: "3.12", sandbox_os: "Linux", sandbox_python: "3.12.13", target_matches_sandbox_python: true, exit_code: simulated || reportStatus === "confirmed_conflict" ? 1 : 0, stdout: simulated ? "SIMULATED" : "No solution found when resolving dependencies", stderr: "", resolved_requirements: null, limitation: null },
    compatibility_report: {
      status: reportStatus,
      summary: reportStatus === "resolved_in_environment" ? "Resolution passed in this environment only." : simulated ? "SIMULATED result." : "Resolver explicitly found no solution.",
      findings: [{ classification: simulated ? "unknown" : reportStatus === "resolved_in_environment" ? "resolved" : "confirmed_conflict", code: "FIXTURE", title: simulated ? "Resolver result is simulated" : reportStatus === "resolved_in_environment" ? "Dependency resolution succeeded in the tested environment" : "Resolver confirmed this dependency set has no solution", explanation: "Installation, imports, and project tests were not run.", packages: [], evidence: [{ source: simulated ? "uv" : "uv", label: simulated ? "SIMULATED resolver output" : "Resolver diagnostic", value: simulated ? "SIMULATED" : "No solution found when resolving dependencies", package: null, source_url: null }] }],
    },
  };
}

describe("CompatibilityReport findings", () => {
  it("shows resolver evidence for a confirmed conflict", () => {
    render(<CompatibilityReport analysis={analysis("confirmed_conflict")} />);
    expect(screen.getByRole("heading", { name: /Original requirements: dependency resolution/ })).toBeInTheDocument();
    expect(screen.getAllByText("confirmed conflict")).toHaveLength(2);
    expect(screen.getAllByText("No solution found when resolving dependencies")).toHaveLength(2);
    expect(screen.getByText("Installation, imports, and project tests were not run.")).toBeInTheDocument();
  });

  it("shows only structured allowlisted resolver failure diagnostics when present", () => {
    const fixture = analysis("confirmed_conflict");
    fixture.resolver = {
      ...fixture.resolver!,
      failure_phase: "file_staging",
      failure_category: "network_failure",
      transport_error_type: "ConnectError",
      transport_cause_type: "gaierror",
      transport_os_error_code: -3,
    };
    render(<CompatibilityReport analysis={fixture} />);
    expect(screen.getByLabelText("Resolver diagnostics")).toHaveTextContent("Failure phase: file staging");
    expect(screen.getByLabelText("Resolver diagnostics")).toHaveTextContent("Failure category: network failure");
    expect(screen.getByLabelText("Resolver diagnostics")).toHaveTextContent("Transport error type: ConnectError");
    expect(screen.getByLabelText("Resolver diagnostics")).toHaveTextContent("OS error code: -3");
  });

  it("omits the resolver diagnostics panel when diagnostic fields are absent", () => {
    render(<CompatibilityReport analysis={analysis("confirmed_conflict")} />);
    expect(screen.queryByLabelText("Resolver diagnostics")).not.toBeInTheDocument();
  });

  it("labels successful resolution as scoped to the tested environment", () => {
    render(<CompatibilityReport analysis={analysis("resolved_in_environment")} />);
    expect(screen.getByText("resolved in environment")).toBeInTheDocument();
    expect(screen.getByText("Dependency resolution succeeded in the tested environment")).toBeInTheDocument();
  });

  it("keeps simulated findings visibly labeled", () => {
    render(<CompatibilityReport analysis={analysis("simulated")} />);
    expect(screen.getAllByText("SIMULATED").length).toBeGreaterThan(0);
    expect(screen.getByText("SIMULATED result.")).toBeInTheDocument();
  });

  it("shows AI explanation as supporting context with evidence references", () => {
    const fixture = analysis("confirmed_conflict");
    fixture.explanation = { status: "available", text: "The resolver reported no solution.", evidence_refs: ["E001"], error_code: null };
    render(<CompatibilityReport analysis={fixture} />);
    expect(screen.getByRole("region", { name: "AI explanation" })).toHaveTextContent("SUPPORTING CONTEXT");
    expect(screen.getByText("References deterministic evidence: E001")).toBeInTheDocument();
    expect(screen.getByText("Deterministic findings above remain Bugsy’s compatibility result.")).toBeInTheDocument();
  });

  it("shows a clear unavailable explanation state without hiding the report", () => {
    const fixture = analysis("resolved_in_environment");
    fixture.explanation = { status: "unavailable", text: null, evidence_refs: [], error_code: "TOKEN_FACTORY_TIMEOUT" };
    render(<CompatibilityReport analysis={fixture} />);
    expect(screen.getByText(/Nemotron explanation is unavailable \(TOKEN_FACTORY_TIMEOUT\)/)).toBeInTheDocument();
    expect(screen.getByText("Dependency resolution succeeded in the tested environment")).toBeInTheDocument();
  });
});
