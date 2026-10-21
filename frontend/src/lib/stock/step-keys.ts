"use client";

import { useRef } from "react";

import { newIdempotencyKey } from "@/lib/api";

// Ключи Idempotency-Key шагов формы склада. Повтор шага после сбоя (ответ потерян) шлёт тот же ключ,
// и сервер отдаёт прежний ответ, а не заводит вторую запись. Ключ шага забывается после его успеха.
export function useStepKeys() {
  const keys = useRef(new Map<string, string>());
  return {
    keyFor(step: string): string {
      let key = keys.current.get(step);
      if (key === undefined) {
        key = newIdempotencyKey();
        keys.current.set(step, key);
      }
      return key;
    },
    forget(step: string): void {
      keys.current.delete(step);
    },
    clear(): void {
      keys.current.clear();
    },
  };
}
