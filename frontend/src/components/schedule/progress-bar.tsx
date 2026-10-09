import { clampPercent, formatPercent } from "@/lib/schedule/format";
import { cn } from "@/lib/utils";

// Полоса готовности; риска «должно быть на сегодня» (planned) нет, если план неизвестен.
export function ProgressBar({
  percent,
  planned,
  label,
  className,
  barClassName,
}: {
  percent: string | number;
  planned?: string | number;
  label?: string;
  className?: string;
  barClassName?: string;
}) {
  const value = clampPercent(percent);
  return (
    <div className={cn("flex items-center gap-2", className)}>
      <div
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(value)}
        className={cn("relative h-2 w-20 shrink-0 overflow-hidden rounded-full bg-muted", barClassName)}
      >
        <div className="h-full rounded-full bg-primary" style={{ width: `${value}%` }} />
        {planned !== undefined ? (
          <div
            className="absolute inset-y-0 w-px bg-foreground/70"
            style={{ left: `${clampPercent(planned)}%` }}
          />
        ) : null}
      </div>
      <span className="w-12 shrink-0 text-right text-xs tabular-nums">{formatPercent(percent)}</span>
    </div>
  );
}
