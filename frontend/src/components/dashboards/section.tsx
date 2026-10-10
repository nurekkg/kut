"use client";

import { useId, type ReactNode } from "react";

export function Section({
  testId,
  title,
  level = "h3",
  children,
}: {
  testId?: string;
  title: string;
  level?: "h2" | "h3";
  children: ReactNode;
}) {
  const headingId = useId();
  const Heading = level;
  return (
    <section data-testid={testId} aria-labelledby={headingId} className="min-w-0 space-y-3">
      <Heading id={headingId} className="text-base font-medium">
        {title}
      </Heading>
      {children}
    </section>
  );
}

export function EmptyNote({ children }: { children: ReactNode }) {
  return <p className="text-sm text-muted-foreground">{children}</p>;
}
