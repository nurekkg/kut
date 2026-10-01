import type {
  LoginResult,
  MfaConfirmResult,
  MfaSetup,
  TokenResponse,
} from "./types";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    readonly retryAfter: number | null = null,
  ) {
    super(code);
    this.name = "ApiError";
  }
}

export async function toApiError(response: Response): Promise<ApiError> {
  let code = response.status === 422 ? "validation" : "unknown";
  try {
    const body: unknown = await response.json();
    if (
      typeof body === "object" &&
      body !== null &&
      "detail" in body &&
      typeof body.detail === "string"
    ) {
      code = body.detail;
    }
  } catch {
    // тело ответа не JSON: остаётся код по статусу
  }
  const retry = Number(response.headers.get("retry-after"));
  return new ApiError(response.status, code, retry > 0 ? retry : null);
}

type PostOptions = { body?: unknown; token?: string };

// Все запросы идут на тот же origin (rewrites Next): cookie refresh-токена ограничена путём /api/v1/auth.
async function post<T>(path: string, { body, token }: PostOptions = {}): Promise<T> {
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await fetch(`/api/v1/auth/${path}`, {
    method: "POST",
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!response.ok) throw await toApiError(response);
  return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
}

export const authApi = {
  login: (identifier: string, password: string) =>
    post<LoginResult>("login", { body: { identifier, password } }),

  verifyMfa: (mfaToken: string, proof: { code: string } | { recovery_code: string }) =>
    post<TokenResponse>("mfa/verify", { body: { mfa_token: mfaToken, ...proof } }),

  setupMfa: (mfaToken: string) =>
    post<MfaSetup>("mfa/setup", { body: { mfa_token: mfaToken } }),

  confirmMfa: (mfaToken: string, code: string) =>
    post<MfaConfirmResult>("mfa/confirm", { body: { mfa_token: mfaToken, code } }),

  refresh: () => post<TokenResponse>("refresh"),

  logout: (token: string | undefined) => post<void>("logout", { token }),
};
