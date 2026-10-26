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
  failure_phase?: "client_configuration" | "file_staging" | "operation_submission" | "operation_tracking" | "python_target_validation" | "resolver_setup" | "resolver_version" | "resolution" | "result_parsing" | null;
  failure_category?: "sandbox_failure" | "network_failure" | "resolver_failure" | "unknown_failure" | null;
  transport_error_type?: "ConnectError" | "ConnectTimeout" | "ReadError" | "ReadTimeout" | "WriteError" | "WriteTimeout" | "PoolTimeout" | "RemoteProtocolError" | "LocalProtocolError" | "ProxyError" | "UnsupportedProtocol" | "NetworkError" | "TimeoutException" | null;
  transport_cause_type?: "ConnectError" | "ConnectTimeout" | "ReadError" | "ReadTimeout" | "WriteError" | "WriteTimeout" | "PoolTimeout" | "RemoteProtocolError" | "LocalProtocolError" | "ProxyError" | "UnsupportedProtocol" | "NetworkError" | "gaierror" | "herror" | "SSLError" | "SSLCertVerificationError" | "ConnectionError" | "ConnectionRefusedError" | "ConnectionResetError" | "ConnectionAbortedError" | "TimeoutError" | "OSError" | "BrokenPipeError" | null;
  transport_os_error_code?: number | null;
};

export type CompatibilityEvidence = {
  source: "pypi" | "uv" | "analysis";
  label: string;
  value: string;
  package: string | null;
  source_url: string | null;
};

export type CompatibilityFinding = {
  classification: "confirmed_conflict" | "potential_issue" | "unknown" | "incomplete" | "resolved";
  code: string;
  title: string;
  explanation: string;
  packages: string[];
  evidence: CompatibilityEvidence[];
};

export type CompatibilityReportData = {
  status: "confirmed_conflict" | "potential_issues" | "unknown" | "incomplete" | "resolved_in_environment" | "simulated";
  summary: string;
  findings: CompatibilityFinding[];
};

export type ExplanationResult = {
  status: "available" | "unavailable" | "not_requested";
  text: string | null;
  evidence_refs: string[];
  error_code: string | null;
};

export type RepairChange = {
  package: string;
  line: number;
  before: string;
  after: string;
  candidate_version: string;
  pypi_source_url: string;
  pypi_requires_python: string | null;
  reason: string;
  evidence_refs: string[];
};

export type RepairProposal = {
  status: "queued" | "running" | "awaiting_resolution" | "ready" | "unavailable";
  proposal_id: string | null;
  summary: string | null;
  original_sha256: string;
  diff_sha256: string | null;
  proposed_requirements: string | null;
  diff: string | null;
  changes: RepairChange[];
  resolver_evidence: {
    status: "accepted" | "rejected" | "not_run" | "unknown";
    resolver: string | null;
    resolver_version: string | null;
    exit_code: number | null;
    python_target: string;
    sandbox_os: string | null;
    sandbox_python: string | null;
    failure_category: "sandbox_failure" | "network_failure" | "resolver_failure" | "unknown_failure" | null;
  } | null;
  resolver_diagnostics?: ResolverResult | null;
  candidate_evidence?: { candidate_id: string; package: string; version: string; source_url: string; requires_python: string | null; requires_dist: string[]; yanked: boolean }[];
  resolver_operation?: Record<string, unknown> | null;
  verified: false;
  error_code: string | null;
};

export type RepairRequestPreview = {
  preview_id: string;
  preview_sha256: string;
  model_id: string;
  base_url: string;
  system_prompt: string;
  evidence_json: string;
  temperature: number;
  max_tokens: number;
  timeout_seconds: number;
};

export type VerificationCheck = {
  status: "passed" | "failed" | "not_run" | "skipped";
  exit_code: number | null;
  stdout: string;
  stderr: string;
  reason: string | null;
};

export type VerificationResult = {
  verification_id: string;
  analysis_id: string;
  proposal_id: string;
  diff_sha256: string;
  status: "queued" | "running" | "complete" | "incomplete" | "unavailable";
  execution_mode: "real" | "not_run";
  environment: {
    sandbox_provider: "contree" | null;
    os: string | null;
    python_version: string | null;
    requested_python: string;
    target_matches_sandbox_python: boolean | null;
  };
  resolver: string | null;
  resolver_version: string | null;
  failure_category: "sandbox_failure" | "network_failure" | "resolver_failure" | "unknown_failure" | null;
  checks: {
    resolution: VerificationCheck;
    installation: VerificationCheck;
    imports: VerificationCheck;
    project_tests: VerificationCheck;
  };
  limitation: string | null;
};

export type InstallationStageResult = {
  stage_id: string;
  analysis_id: string;
  proposal_id: string;
  diff_sha256: string;
  stage: "installation";
  status: "running" | "passed" | "failed" | "incomplete" | "unknown";
  execution_mode: "real" | "not_run";
  approval_state: "consumed";
  environment: {
    sandbox_provider: "contree" | null;
    os: string | null;
    python_version: string | null;
    requested_python: string;
    target_matches_sandbox_python: boolean | null;
  };
  uv_version: string | null;
  exit_code: number | null;
  stdout: string;
  stderr: string;
  failure_category: "sandbox_failure" | "network_failure" | "installation_failure" | "unknown_failure" | null;
  failure_phase?: string | null;
  failure_detail?: string | null;
  artifact_available?: boolean;
  limitation: string | null;
  created_at: string;
  updated_at: string;
};

export type ImportStageResult = Omit<InstallationStageResult, "stage" | "uv_version" | "artifact_available" | "failure_category"> & {
  stage: "imports";
  failure_category: "sandbox_failure" | "network_failure" | "import_failure" | "unknown_failure" | null;
};

export type InstallationOperationPreview = {
  analysis_id: string; proposal_id: string; diff_sha256: string; stage: "installation";
  requirements_sha256: string; requirements_bytes_base64: string; requirements_byte_length: number;
  target_python: string; sandbox_image: string; resolver_tool: "uv"; resolver_version: string;
  command: string; args: string[]; env: Record<string, string>; cwd: string; input_path: string;
  timeout_seconds: number; output_limit_bytes: number; retain_image: boolean; image_disposable: boolean;
  retention_limitation: string; sandbox_enabled: boolean; operation_count: 1; automatic_retries: false;
};

export type ImportOperationPreview = {
  analysis_id: string; proposal_id: string; diff_sha256: string; stage: "imports";
  installation_stage_id: string; sandbox_image_reference: string; target_python: string;
  sandbox_os: string | null; sandbox_python: string | null;
  import_mappings: Array<{ package: string; module: string }>;
  command: string; args: string[]; env: Record<string, string>; cwd: string;
  timeout_seconds: number; output_limit_bytes: number; image_disposable: boolean;
  operation_count: 1; automatic_retries: false; limitation: string;
};

export type AnalysisResponse = {
  analysis_id: string;
  status: "queued" | "running" | "complete" | "analysis_incomplete";
  source_id: string;
  python_target: string;
  requirements: ParsedRequirement[];
  metadata: MetadataResult[];
  resolver: ResolverResult | null;
  compatibility_report?: CompatibilityReportData | null;
  explanation?: ExplanationResult | null;
  repair_proposal?: RepairProposal | null;
  errors: string[];
  poll_url: string | null;
};
