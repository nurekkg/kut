export type Me = {
  id: string;
  full_name: string;
  role: string;
  scope: string;
  is_internal: boolean;
  counterparty_id: string | null;
  permissions: string[];
  project_ids: string[];
  amr: string[];
};

export type TokenResponse = {
  status: "ok";
  access_token: string;
  token_type: "bearer";
  expires_in: number;
  user: Me;
};

export type MfaChallenge = {
  status: "mfa_required" | "mfa_enrollment_required";
  mfa_token: string;
  expires_in: number;
};

export type LoginResult = TokenResponse | MfaChallenge;

export type MfaSetup = { secret: string; otpauth_uri: string };

export type MfaConfirmResult = {
  recovery_codes: string[];
  session: TokenResponse | null;
};

export type Project = {
  id: string;
  name: string;
  status: string;
  legal_entity_id: string;
};
