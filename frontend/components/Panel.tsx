import type { ReactNode } from "react";

interface PanelProps {
  title: string;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
  testId?: string;
}

export default function Panel({ title, right, children, className = "", bodyClassName = "", testId }: PanelProps) {
  return (
    <section
      data-testid={testId}
      className={`flex min-h-0 min-w-0 flex-col border border-line bg-panel ${className}`}
    >
      <header className="flex h-7 shrink-0 items-center justify-between border-b border-line-soft px-2.5">
        <h2 className="text-[11px] font-semibold tracking-wide text-muted">{title}</h2>
        {right}
      </header>
      <div className={`min-h-0 flex-1 ${bodyClassName}`}>{children}</div>
    </section>
  );
}
