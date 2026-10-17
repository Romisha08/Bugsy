export type HealthResponse = {
  status: string;
  service: string;
};

const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");

export async function fetchHealth(): Promise<HealthResponse> {
  const response = await fetch(`${apiBaseUrl}/health`, {
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new Error(`API returned HTTP ${response.status}`);
  }
  return (await response.json()) as HealthResponse;
}
