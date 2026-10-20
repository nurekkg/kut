export const NOTIFICATIONS_API = "/api/v1/notifications";

export const DELIVERIES_KEY = ["notifications", "deliveries"] as const;
export const NOTIFY_SETTINGS_KEY = ["notifications", "settings"] as const;

export const CONSENT_TEXT_VERSION = "2026-10-20-v1";

export type Delivery = {
  id: string;
  created_at: string;
  event_type: string;
  template_code: string;
  attempt: number;
  status: string;
  error_code: string | null;
  phone_masked: string;
  document_id: string | null;
  document_number: string | null;
  recipient_role: string | null;
  recipient_role_name: string | null;
  defer_until: string | null;
};

export type NotifySettings = {
  notify_whatsapp: boolean;
  can_notify: boolean;
  consent_at: string | null;
  revoked_at: string | null;
  consent_text_version: string | null;
};
