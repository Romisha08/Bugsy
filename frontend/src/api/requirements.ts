import type { ApiErrorBody, ParseResponse } from "../types/requirements";

export class BugsyApiError extends Error {
  constructor(readonly status: number, readonly body: ApiErrorBody | null) {
    super(body?.message ?? `Bugsy API returned HTTP ${status}.`);
  }
}

const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");

async function parseResponse(response: Response): Promise<ParseResponse> {
  const body = await response.json();
  if (!response.ok) throw new BugsyApiError(response.status, body?.error ?? null);
  return body as ParseResponse;
}

export async function parseRequirementsText(text: string, pythonVersion: string): Promise<ParseResponse> {
  return parseResponse(await fetch(`${apiBaseUrl}/api/v1/requirements/parse`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ text, python_version: pythonVersion }),
  }));
}

export async function parseRequirementsFile(file: File, pythonVersion: string): Promise<ParseResponse> {
  const form = new FormData();
  form.set("file", file, file.name);
  form.set("python_version", pythonVersion);
  return parseResponse(await fetch(`${apiBaseUrl}/api/v1/requirements/parse`, {
    method: "POST",
    headers: { Accept: "application/json" },
    body: form,
  }));
}
