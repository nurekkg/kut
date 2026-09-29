export const CHECK_NAMES = ["db", "redis", "s3"] as const;

export type CheckName = (typeof CHECK_NAMES)[number];
export type CheckStatus = "ok" | "fail";

export type ReadyResponse = {
  status: "ok" | "degraded";
  checks: Partial<Record<CheckName, CheckStatus>>;
};

export async function fetchReadiness(): Promise<ReadyResponse> {
  const response = await fetch("/api/health/ready", { cache: "no-store" });
  if (response.status !== 200 && response.status !== 503) {
    throw new Error(`Unexpected status ${response.status}`);
  }
  return (await response.json()) as ReadyResponse;
}
