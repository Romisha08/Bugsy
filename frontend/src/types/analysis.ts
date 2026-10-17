import type { ParsedRequirement } from "./requirements";

export type MetadataResult = {
  package: string;
  status: "available" | "unavailable" | "error";
  source_url: string | null;
  release: string | null;
  latest_version: string | null;
  requires_python: string | null;
  requires_dist: string[];
  error_code: string | null;
  error_message: string | null;
  retrieved_at: string;
};

export type ResolverResult = {
  status: "not_run" | "resolved" | "unresolved" | "error";
  execution_mode: "real" | "simulated" | "not_run";
  resolver: string | null;
  resolver_version: string | null;
  python_target: string;
  sandbox_os: string | null;
  sandbox_python: string | null;
  target_matches_sandbox_python: boolean | null;
  exit_code: number | null;
  stdout: string;
  stderr: string;
  resolved_requirements: string | null;
  limitation: string | null;
};

export type AnalysisResponse = {
  analysis_id: string;
  status: "queued" | "running" | "complete" | "analysis_incomplete";
  source_id: string;
  python_target: string;
  requirements: ParsedRequirement[];
  metadata: MetadataResult[];
  resolver: ResolverResult | null;
  errors: string[];
  poll_url: string | null;
};
