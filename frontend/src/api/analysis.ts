import type { AnalysisResponse } from "../types/analysis";
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
