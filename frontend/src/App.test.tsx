import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import App from "./App";
import { CompatibilityReport } from "./components/CompatibilityReport";
import type { AnalysisResponse } from "./types/analysis";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const parsedPayload = {
  source_id: "source-1",
  parse_status: "parsed" as const,
  python_version: "3.12",
  requirements: [{ line: 1, original: "requests>=2", name: "requests", specifier: ">=2", extras: [], marker: null }],
  warnings: [],
  source_sha256: "a".repeat(64),
};

describe("requirements workflow", () => {
  it("shows the API connection and a read-only parsed preview", async () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/health")) return Promise.resolve({ ok: true, json: async () => ({ status: "ok", service: "bugsy-api" }) });
      return Promise.resolve({ ok: true, json: async () => parsedPayload });
    }));
    render(<App />);

    fireEvent.change(screen.getByLabelText("Paste requirements.txt contents"), { target: { value: "requests>=2" } });
    fireEvent.click(screen.getByRole("button", { name: /parse requirements/i }));

    await waitFor(() => expect(screen.getByRole("heading", { name: "Parsed requirements" })).toBeInTheDocument());
    expect(screen.getByText("requests", { selector: "strong" })).toBeInTheDocument();
    expect(screen.getByText("Parsing confirms Bugsy understood the file. It does not mean the dependencies are compatible.")).toBeInTheDocument();
    expect(await screen.findByRole("status")).toHaveTextContent("Backend connected");
  });

  it("shows affected lines and prevents analysis after a parse error", async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/health")) return Promise.resolve({ ok: true, json: async () => ({ status: "ok", service: "bugsy-api" }) });
      return Promise.resolve({
        ok: false,
        status: 422,
        json: async () => ({ error: { code: "UNSUPPORTED_REQUIREMENTS", message: "Fix the listed lines.", details: [{ line: 4, original: "-c constraints.txt", reason: "Constraint file includes are not supported." }] } }),
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    fireEvent.change(screen.getByLabelText("Paste requirements.txt contents"), { target: { value: "requests>=2\n-c constraints.txt" } });
    fireEvent.click(screen.getByRole("button", { name: /parse requirements/i }));

    expect(await screen.findByText("Constraint file includes are not supported.")).toBeInTheDocument();
    expect(screen.getByText("-c constraints.txt")).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/api/v1/analyses"))).toBe(false);
  });

  it("renders simulated resolver output as simulated, never as real verification", () => {
    const analysis: AnalysisResponse = {
      analysis_id: "analysis-1", status: "complete", source_id: "source-1", python_target: "3.12",
      requirements: parsedPayload.requirements, metadata: [], errors: [], poll_url: null,
      resolver: {
        status: "unresolved", execution_mode: "simulated", resolver: "uv", resolver_version: "simulated",
        python_target: "3.12", sandbox_os: null, sandbox_python: null, target_matches_sandbox_python: null,
        exit_code: 1, stdout: "SIMULATED resolver outcome: unresolved", stderr: "", resolved_requirements: null,
        limitation: "Simulated test result only. It is not a real dependency resolution.",
      },
    };
    render(<CompatibilityReport analysis={analysis} />);
    expect(screen.getByText("SIMULATED")).toBeInTheDocument();
    expect(screen.getByText("Simulated test result only. It is not a real dependency resolution.")).toBeInTheDocument();
  });
});
