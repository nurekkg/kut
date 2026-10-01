"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { FullPageSpinner } from "@/components/auth/auth-gate";
import { DEFAULT_PATH } from "@/lib/auth/next-path";

export function HomeRedirect() {
  const { status } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (status === "authenticated") router.replace(DEFAULT_PATH);
    if (status === "anonymous") router.replace("/login");
  }, [status, router]);

  return <FullPageSpinner />;
}
