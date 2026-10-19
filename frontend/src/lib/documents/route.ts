// Роли шагов маршрута (ADR-015 часть 5, ТЗ §5.4): те же, что допускает сервер в шаблоне.
export const ROUTE_ROLE_CODES = [
  "foreman",
  "estimator",
  "technical_director",
  "finance_director",
  "owner",
] as const;

export type RouteRoleCode = (typeof ROUTE_ROLE_CODES)[number];

export function isRouteRoleCode(code: string): code is RouteRoleCode {
  return (ROUTE_ROLE_CODES as readonly string[]).includes(code);
}
