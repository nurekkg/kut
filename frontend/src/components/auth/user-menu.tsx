"use client";

import { LogOut, Settings } from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";

import { useAuth } from "@/components/auth/auth-provider";
import { useLogout } from "@/components/auth/logout-guard";
import { Button, buttonVariants } from "@/components/ui/button";
import { isRole } from "@/lib/roles";

export function UserMenu() {
  const t = useTranslations("Auth");
  const roles = useTranslations("Roles");
  const settings = useTranslations("Settings");
  const { user } = useAuth();
  const { request: logout, leaving: pending, dialog: logoutDialog } = useLogout();

  if (!user) return null;

  return (
    <div className="flex items-center justify-between gap-3">
      {logoutDialog}
      <div className="min-w-0">
        <p className="truncate text-sm font-medium" data-testid="user-name">
          {user.full_name}
        </p>
        <p className="truncate text-sm text-muted-foreground" data-testid="user-role">
          {isRole(user.role) ? roles(user.role) : user.role}
        </p>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {user.permissions.includes("settings:admin") ? (
          <Link
            href="/settings/legal-entities"
            className={buttonVariants({ variant: "outline", size: "lg", className: "h-10" })}
          >
            <Settings data-icon="inline-start" aria-hidden />
            {settings("title")}
          </Link>
        ) : null}
        <Link
          href="/profile"
          className={buttonVariants({ variant: "outline", size: "lg", className: "h-10" })}
        >
          {t("profile")}
        </Link>
        <Button variant="outline" size="lg" className="h-10" onClick={() => void logout()} disabled={pending}>
          <LogOut data-icon="inline-start" aria-hidden />
          {t("logout")}
        </Button>
      </div>
    </div>
  );
}
