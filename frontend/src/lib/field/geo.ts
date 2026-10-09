"use client";

import { useEffect, useState } from "react";

export type GeoState =
  | { status: "locating" }
  | { status: "ready"; latitude: number; longitude: number; accuracy: number }
  | { status: "denied" }
  | { status: "unavailable" }
  | { status: "timeout" };

// Точность хуже 100 м показываем с предупреждением: для факта на площадке этого мало.
export const POOR_ACCURACY_M = 100;

const LOCATING: GeoState = { status: "locating" };

function unsupported(): boolean {
  return typeof navigator === "undefined" || !("geolocation" in navigator);
}

// Геопозиция определяется сама при открытии шага; отказ или сбой не блокирует отправку факта.
// attempt растёт при повторном запросе («Определить снова»): пока нет ответа на него, состояние
// «определяем», а не результат прошлой попытки.
export function useGeolocation(attempt: number): GeoState {
  const [result, setResult] = useState<{ attempt: number; value: GeoState } | null>(null);

  useEffect(() => {
    if (unsupported()) return;
    let cancelled = false;
    const finish = (value: GeoState) => {
      if (!cancelled) setResult({ attempt, value });
    };
    navigator.geolocation.getCurrentPosition(
      (position) =>
        finish({
          status: "ready",
          latitude: position.coords.latitude,
          longitude: position.coords.longitude,
          accuracy: position.coords.accuracy,
        }),
      (error) => {
        if (error.code === error.PERMISSION_DENIED) finish({ status: "denied" });
        else if (error.code === error.TIMEOUT) finish({ status: "timeout" });
        else finish({ status: "unavailable" });
      },
      { enableHighAccuracy: true, timeout: 20_000, maximumAge: 30_000 },
    );
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  if (unsupported()) return { status: "unavailable" };
  return result !== null && result.attempt === attempt ? result.value : LOCATING;
}
