"use client";

import { LogOut } from "lucide-react";
import { useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { Button } from "@/components/ui/button";

const ROLES = [
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

type RoleCode = (typeof ROLES)[number];

function isRole(value: string): value is RoleCode {
  return (ROLES as readonly string[]).includes(value);
}

export function UserMenu() {
  const t = useTranslations("Auth");
  const roles = useTranslations("Roles");
  const { user, signOut } = useAuth();
  const router = useRouter();
  const [pending, setPending] = useState(false);

  if (!user) return null;

  async function logout() {
    setPending(true);
    try {
      await signOut();
    } finally {
      router.replace("/login");
    }
  }

  return (
    <div className="flex items-center justify-between gap-3">
      <div className="min-w-0">
        <p className="truncate text-sm font-medium" data-testid="user-name">
          {user.full_name}
        </p>
        <p className="truncate text-sm text-muted-foreground" data-testid="user-role">
          {isRole(user.role) ? roles(user.role) : user.role}
        </p>
      </div>
      <Button variant="outline" size="lg" className="h-10 shrink-0" onClick={() => void logout()} disabled={pending}>
        <LogOut data-icon="inline-start" aria-hidden />
        {t("logout")}
      </Button>
    </div>
  );
}
