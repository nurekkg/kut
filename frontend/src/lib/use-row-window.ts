"use client";

import { useEffect, useState, type RefObject } from "react";

export type RowWindow = { start: number; end: number };

// Окно видимых строк списка фиксированной высоты. Строк тысячи, а в DOM их десятки: остальные
// заменяют распорки. Высота строки постоянная, поэтому измерять каждую строку не нужно.
// offset - высота закреплённой шапки внутри области прокрутки.
export function useRowWindow(
  scroller: RefObject<HTMLElement | null>,
  count: number,
  rowHeight: number,
  { overscan = 12, offset = 0 }: { overscan?: number; offset?: number } = {},
): RowWindow {
  const [view, setView] = useState({ top: 0, height: 0 });

  useEffect(() => {
    const element = scroller.current;
    if (!element) return;
    let frame = 0;
    const read = () => {
      frame = 0;
      setView((previous) =>
        previous.top === element.scrollTop && previous.height === element.clientHeight
          ? previous
          : { top: element.scrollTop, height: element.clientHeight },
      );
    };
    const schedule = () => {
      if (frame === 0) frame = requestAnimationFrame(read);
    };
    // ResizeObserver сообщает размер сразу после подключения: первое измерение приходит оттуда.
    const observer = new ResizeObserver(schedule);
    observer.observe(element);
    element.addEventListener("scroll", schedule, { passive: true });
    return () => {
      observer.disconnect();
      element.removeEventListener("scroll", schedule);
      if (frame !== 0) cancelAnimationFrame(frame);
    };
  }, [scroller]);

  const first = Math.max(0, Math.floor((view.top - offset) / rowHeight) - overscan);
  const last = Math.min(count, Math.ceil((view.top + view.height - offset) / rowHeight) + overscan);
  return { start: Math.min(first, count), end: Math.max(Math.min(first, count), last) };
}
