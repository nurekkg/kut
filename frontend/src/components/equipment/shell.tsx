import type { ReactNode } from "react";

import { AuthGate } from "@/components/auth/auth-gate";
import { UserMenu } from "@/components/auth/user-menu";
import { EquipmentNav } from "@/components/equipment/equipment-nav";
import { AppNav } from "@/components/projects/app-nav";

// Общая оболочка экранов техники: авторизация, главная навигация, разделы модуля.
export function EquipmentShell({ children }: { children: ReactNode }) {
  return (
    <AuthGate>
      <main className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-5 px-4 py-6">
        <UserMenu />
        <AppNav />
        <EquipmentNav />
        {children}
      </main>
    </AuthGate>
  );
}
