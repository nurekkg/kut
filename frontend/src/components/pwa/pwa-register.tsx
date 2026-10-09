"use client";

import { useEffect } from "react";

// Регистрирует service worker только в боевой сборке: в разработке он мешал бы обновлению страниц.
export function PwaRegister() {
  useEffect(() => {
    if (process.env.NODE_ENV !== "production" || !("serviceWorker" in navigator)) return;
    navigator.serviceWorker
      .register("/sw.js", { scope: "/" })
      .catch((error: unknown) => console.warn("service worker не зарегистрирован", error));
  }, []);
  return null;
}
