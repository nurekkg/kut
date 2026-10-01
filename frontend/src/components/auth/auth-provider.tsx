"use client";

import { useQueryClient } from "@tanstack/react-query";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useSyncExternalStore,
  type ReactNode,
} from "react";

import {
  clearSession,
  endSession,
  refreshSession,
  sessionStore,
  startSession,
} from "@/lib/auth/session";
import type { Me, TokenResponse } from "@/lib/auth/types";

type AuthContextValue = {
  status: "loading" | "authenticated" | "anonymous";
  user: Me | null;
  signIn: (response: TokenResponse) => void;
  signOut: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

// Токен обновляется заранее, за минуту до истечения; если вкладка «спала», хватит повтора по 401.
const REFRESH_AHEAD_MS = 60_000;
const MIN_DELAY_MS = 5_000;

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const state = useSyncExternalStore(
    sessionStore.subscribe,
    sessionStore.get,
    sessionStore.get,
  );

  useEffect(() => {
    void refreshSession();
  }, []);

  const expiresAt = state.session?.expiresAt;
  useEffect(() => {
    if (expiresAt === undefined) return;
    const delay = Math.max(MIN_DELAY_MS, expiresAt - Date.now() - REFRESH_AHEAD_MS);
    const timer = window.setTimeout(() => void refreshSession(), delay);
    return () => window.clearTimeout(timer);
  }, [expiresAt]);

  const signIn = useCallback(
    (response: TokenResponse) => {
      queryClient.clear(); // данные прошлого пользователя не должны попасть в кэш нового
      startSession(response);
    },
    [queryClient],
  );

  const signOut = useCallback(async () => {
    try {
      await endSession();
    } finally {
      clearSession();
      queryClient.clear();
    }
  }, [queryClient]);

  const value = useMemo<AuthContextValue>(
    () => ({
      status: !state.ready ? "loading" : state.session ? "authenticated" : "anonymous",
      user: state.session?.user ?? null,
      signIn,
      signOut,
    }),
    [state, signIn, signOut],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (value === null) throw new Error("useAuth must be used inside AuthProvider");
  return value;
}
