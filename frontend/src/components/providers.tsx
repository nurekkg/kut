"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

import { AuthProvider } from "@/components/auth/auth-provider";
import { ApiError } from "@/lib/auth/api";

// Отказ в доступе повторять бессмысленно: 401 уже обработан обновлением сессии, 403 не пройдёт и со второй попытки.
function shouldRetry(failureCount: number, error: Error): boolean {
  if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
    return false;
  }
  return failureCount < 1;
}

export function Providers({ children }: { children: ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: { queries: { staleTime: 5_000, retry: shouldRetry } },
      }),
  );

  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>{children}</AuthProvider>
    </QueryClientProvider>
  );
}
