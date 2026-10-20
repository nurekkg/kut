export const COUNTERPARTY_TYPES = ["customer", "subcontractor", "supplier", "investor"] as const;
export type CounterpartyType = (typeof COUNTERPARTY_TYPES)[number];

export type LegalEntity = {
  id: string;
  name: string;
  tax_id: string | null;
  project_count: number;
};

export type Counterparty = {
  id: string;
  name: string;
  tax_id: string | null;
  types: string[];
};

export type Role = {
  id: string;
  code: string;
  name: string;
  scope: string;
  requires_2fa: boolean;
};

export type ProjectRef = { id: string; name: string; legal_entity_id: string };

export type AdminUser = {
  id: string;
  full_name: string;
  email: string | null;
  phone: string | null;
  role_id: string;
  role: string;
  role_name: string;
  scope: string;
  is_active: boolean;
  mfa_enabled: boolean;
  requires_2fa: boolean;
  pending_activation: boolean;
  counterparty_id: string | null;
  counterparty_name: string | null;
  project_ids: string[];
  legal_entity_ids: string[];
  notify_whatsapp: boolean;
  notify_whatsapp_consent_at: string | null;
  notify_whatsapp_consent_version: string | null;
};

export type Invite = { path: string; expires_at: string };

export type AuthEvent = {
  id: number;
  occurred_at: string;
  action: string;
  actor_id: string | null;
  actor_name: string | null;
  actor_kind: string;
  ip: string | null;
  subject_id: string | null;
  subject_name: string | null;
  meta: Record<string, unknown>;
};

export type EventPage = { items: AuthEvent[]; next_before: number | null };

// Коды ошибок API настроек, для которых есть текст в сообщениях.
export const KNOWN_ERRORS = [
  "not_found",
  "forbidden",
  "legal_entity_in_use",
  "counterparty_in_use",
  "email_taken",
  "phone_taken",
  "invalid_phone",
  "invalid_email",
  "contact_required",
  "invalid_counterparty",
  "role_scope_mismatch",
  "group_role_has_all_access",
  "external_has_no_legal_entity_access",
  "unknown_project",
  "unknown_legal_entity",
  "idempotency_key_reused",
  "request_in_progress",
  "validation",
] as const;

export const EVENT_ACTIONS = [
  "login_success",
  "login_failed",
  "logout",
  "refresh_reuse",
  "refresh_grace",
  "mfa_failed",
  "mfa_enabled",
  "mfa_reset",
  "recovery_code_used",
  "password_changed",
  "role_assigned",
  "user_created",
  "user_deactivated",
  "user_activated",
  "invite_issued",
  "invite_used",
] as const;
export type EventAction = (typeof EVENT_ACTIONS)[number];
