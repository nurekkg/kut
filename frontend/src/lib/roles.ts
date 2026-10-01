export const ROLES = [
  "owner",
  "finance_director",
  "technical_director",
  "foreman",
  "master",
  "estimator",
  "procurement",
  "storekeeper",
  "accountant",
  "lawyer",
  "customer",
  "subcontractor",
  "supplier",
] as const;

export type RoleCode = (typeof ROLES)[number];

export function isRole(value: string): value is RoleCode {
  return (ROLES as readonly string[]).includes(value);
}
