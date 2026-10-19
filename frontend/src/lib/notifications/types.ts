export const NOTIFICATIONS_API = "/api/v1/notifications";

export const DELIVERIES_KEY = ["notifications", "deliveries"] as const;
export const NOTIFY_SETTINGS_KEY = ["notifications", "settings"] as const;

export type Delivery = {
  id: string;
  created_at: string;
  event_type: string;
  template_code: string;
  attempt: number;
  status: string;
  error_code: string | null;
  phone_masked: string;
};

export type NotifySettings = {
  notify_whatsapp: boolean;
  can_notify: boolean;
};
