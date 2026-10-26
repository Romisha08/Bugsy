import type { AnalysisResponse, ImportOperationPreview, ImportStageResult, InstallationOperationPreview, InstallationStageResult, RepairRequestPreview, VerificationResult } from "../types/analysis";
import { BugsyApiError } from "./requirements";

const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");

async function readAnalysisResponse(response: Response): Promise<AnalysisResponse> {
  const body = await response.json();
  if (!response.ok) throw new BugsyApiError(response.status, body?.error ?? null);
  return body as AnalysisResponse;
}

export async function startAnalysis(sourceId: string): Promise<AnalysisResponse> {
  return readAnalysisResponse(await fetch(`${apiBaseUrl}/api/v1/analyses`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ source_id: sourceId }),
  }));
}

export async function fetchAnalysis(analysisId: string): Promise<AnalysisResponse> {
  return readAnalysisResponse(await fetch(`${apiBaseUrl}/api/v1/analyses/${encodeURIComponent(analysisId)}`, {
    headers: { Accept: "application/json" },
  }));
}

export async function previewRepairRequest(analysisId: string): Promise<RepairRequestPreview> {
  const response = await fetch(`${apiBaseUrl}/api/v1/analyses/${encodeURIComponent(analysisId)}/repair-request-preview`, { method: "POST", headers: { Accept: "application/json" } });
  const body = await response.json();
  if (!response.ok) throw new BugsyApiError(response.status, body?.error ?? null);
  return body as RepairRequestPreview;
}

export async function requestRepairProposal(analysisId: string, previewSha256: string): Promise<AnalysisResponse> {
  return readAnalysisResponse(await fetch(`${apiBaseUrl}/api/v1/analyses/${encodeURIComponent(analysisId)}/repair-proposal`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ approved: true, preview_sha256: previewSha256 }),
  }));
}

export async function resolveRepairProposal(analysisId: string, proposalId: string, diffSha256: string): Promise<AnalysisResponse> {
  return readAnalysisResponse(await fetch(`${apiBaseUrl}/api/v1/analyses/${encodeURIComponent(analysisId)}/repair-proposal/${encodeURIComponent(proposalId)}/resolve`, {
    method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ approved: true, proposal_id: proposalId, diff_sha256: diffSha256 }),
  }));
}

async function readVerificationResponse(response: Response): Promise<VerificationResult> {
  const body = await response.json();
  if (!response.ok) throw new BugsyApiError(response.status, body?.error ?? null);
  return body as VerificationResult;
}

export async function startVerification(analysisId: string, proposalId: string, diffSha256: string): Promise<VerificationResult> {
  return readVerificationResponse(await fetch(apiBaseUrl + "/api/v1/analyses/" + encodeURIComponent(analysisId) + "/verification", {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ proposal_id: proposalId, approved: true, diff_sha256: diffSha256 }),
  }));
}

export async function fetchVerification(verificationId: string): Promise<VerificationResult> {
  return readVerificationResponse(await fetch(apiBaseUrl + "/api/v1/verifications/" + encodeURIComponent(verificationId), {
    headers: { Accept: "application/json" },
  }));
}

async function readInstallationStageResponse(response: Response): Promise<InstallationStageResult> {
  const body = await response.json();
  if (!response.ok) throw new BugsyApiError(response.status, body?.error ?? null);
  return body as InstallationStageResult;
}

export async function fetchLatestInstallationStage(analysisId: string, proposalId: string): Promise<InstallationStageResult | null> {
  const query = new URLSearchParams({ proposal_id: proposalId });
  const response = await fetch(`${apiBaseUrl}/api/v1/analyses/${encodeURIComponent(analysisId)}/verification/installation?${query.toString()}`, {
    headers: { Accept: "application/json" },
  });
  const body = await response.json();
  if (!response.ok) throw new BugsyApiError(response.status, body?.error ?? null);
  return body as InstallationStageResult | null;
}

export async function startInstallationStage(analysisId: string, proposalId: string, diffSha256: string): Promise<InstallationStageResult> {
  return readInstallationStageResponse(await fetch(apiBaseUrl + "/api/v1/analyses/" + encodeURIComponent(analysisId) + "/verification/installation", {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ stage: "installation", approved: true, proposal_id: proposalId, diff_sha256: diffSha256 }),
  }));
}

export async function fetchInstallationStage(stageId: string): Promise<InstallationStageResult> {
  return readInstallationStageResponse(await fetch(apiBaseUrl + "/api/v1/verification-stages/" + encodeURIComponent(stageId), {
    headers: { Accept: "application/json" },
  }));
}

async function readJson<T>(response: Response): Promise<T> {
  const body = await response.json();
  if (!response.ok) throw new BugsyApiError(response.status, body?.error ?? null);
  return body as T;
}

const stageBase = (analysisId: string, stage: "installation" | "imports") =>
  `${apiBaseUrl}/api/v1/analyses/${encodeURIComponent(analysisId)}/verification/${stage}`;

export async function fetchInstallationAttempts(analysisId: string, proposalId: string): Promise<InstallationStageResult[]> {
  const query = new URLSearchParams({ proposal_id: proposalId });
  return readJson(await fetch(`${stageBase(analysisId, "installation")}/attempts?${query}`, { headers: { Accept: "application/json" } }));
}

export async function previewInstallationAttempt(analysisId: string, proposalId: string): Promise<InstallationOperationPreview> {
  const query = new URLSearchParams({ proposal_id: proposalId });
  return readJson(await fetch(`${stageBase(analysisId, "installation")}/preview?${query}`, { headers: { Accept: "application/json" } }));
}

export async function startFreshInstallationAttempt(analysisId: string, proposalId: string, diffSha256: string, requestId: string): Promise<InstallationStageResult> {
  return readJson(await fetch(`${stageBase(analysisId, "installation")}/attempts`, {
    method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ stage: "installation", approved: true, proposal_id: proposalId, diff_sha256: diffSha256, request_id: requestId }),
  }));
}

export async function fetchImportAttempts(analysisId: string, proposalId: string): Promise<ImportStageResult[]> {
  const query = new URLSearchParams({ proposal_id: proposalId });
  return readJson(await fetch(`${stageBase(analysisId, "imports")}/attempts?${query}`, { headers: { Accept: "application/json" } }));
}

export async function previewImportAttempt(analysisId: string, proposalId: string): Promise<ImportOperationPreview> {
  const query = new URLSearchParams({ proposal_id: proposalId });
  return readJson(await fetch(`${stageBase(analysisId, "imports")}/preview?${query}`, { headers: { Accept: "application/json" } }));
}

export async function startImportAttempt(analysisId: string, proposalId: string, diffSha256: string, requestId: string): Promise<ImportStageResult> {
  return readJson(await fetch(`${stageBase(analysisId, "imports")}/attempts`, {
    method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ stage: "imports", approved: true, proposal_id: proposalId, diff_sha256: diffSha256, request_id: requestId }),
  }));
}

export async function fetchVerificationAttempt(stageId: string): Promise<InstallationStageResult | ImportStageResult> {
  return readJson(await fetch(`${apiBaseUrl}/api/v1/verification-attempts/${encodeURIComponent(stageId)}`, { headers: { Accept: "application/json" } }));
}

export async function approveProposalDownload(analysisId: string, proposalId: string, diffSha256: string): Promise<void> {
  const response = await fetch(apiBaseUrl + "/api/v1/analyses/" + encodeURIComponent(analysisId) + "/approval", {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ proposal_id: proposalId, approved: true, diff_sha256: diffSha256 }),
  });
  if (!response.ok) {
    const body = await response.json();
    throw new BugsyApiError(response.status, body?.error ?? null);
  }
}

export async function downloadProposal(analysisId: string): Promise<Blob> {
  const response = await fetch(apiBaseUrl + "/api/v1/analyses/" + encodeURIComponent(analysisId) + "/download", {
    headers: { Accept: "text/plain" },
  });
  if (!response.ok) {
    const body = await response.json();
    throw new BugsyApiError(response.status, body?.error ?? null);
  }
  return response.blob();
}
