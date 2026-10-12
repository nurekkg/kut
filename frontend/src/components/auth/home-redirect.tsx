"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { FullPageSpinner } from "@/components/auth/auth-gate";
import { landingPath } from "@/lib/auth/next-path";

export function HomeRedirect() {
  const { status, user } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (status === "authenticated") router.replace(landingPath(user));
    if (status === "anonymous") router.replace("/login");
  }, [status, user, router]);

  return <FullPageSpinner />;
}
