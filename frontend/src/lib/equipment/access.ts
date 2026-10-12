"use client";

import { useAuth } from "@/components/auth/auth-provider";
import type { Me } from "@/lib/auth/types";

// Права экранов повторяют матрицу (seeds/rbac_matrix.yaml). Это только подсказка интерфейсу:
// решает API и RLS, а скрытая кнопка не заменяет проверку на сервере.
export type EquipmentAccess = {
  isMachinist: boolean;
  canRead: boolean;
  canRegistry: boolean;
  canRates: boolean;
  canWrite: boolean;
  canMaintenance: boolean;
  canRequest: boolean;
  canAssign: boolean;
  canApproveRate: boolean;
  canFinanceWrite: boolean;
  canContracts: boolean;
  canObjects: boolean;
  userId: string | null;
};

export function equipmentAccess(user: Me | null): EquipmentAccess {
  const has = (permission: string) => user?.permissions.includes(permission) ?? false;
  const isMachinist = user?.role === "machinist";
  const canRead = has("equipment:read");
  const canRates = has("equipment:read:rates");
  const canWrite = has("equipment:write");
  return {
    isMachinist,
    canRead,
    canRegistry: canRead && !isMachinist,
    canRates,
    canWrite,
    canMaintenance: has("equipment:write:maintenance"),
    canRequest: has("equipment:write:request"),
    canAssign: has("equipment:approve:assign"),
    canApproveRate: has("equipment:approve:rate"),
    canFinanceWrite: has("finance:write"),
    canContracts: canRates || canWrite,
    canObjects: has("objects:read"),
    userId: user?.id ?? null,
  };
}

export function useEquipmentAccess(): EquipmentAccess {
  const { user } = useAuth();
  return equipmentAccess(user);
}
