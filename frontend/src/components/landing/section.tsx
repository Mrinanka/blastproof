import { cn } from "@/lib/utils";

// One idea per section: a mono eyebrow, an h2 that labels the region, an optional intro.
export function Section({
  id,
  eyebrow,
  title,
  intro,
  className,
  split = false,
  children,
}: {
  id: string;
  eyebrow: string;
  title: string;
  intro?: React.ReactNode;
  className?: string;
  /** Heading on the left and content on the right on wide screens (the FAQ). */
  split?: boolean;
  children: React.ReactNode;
}) {
  const headingId = `${id}-title`;
  return (
    <section
      id={id}
      aria-labelledby={headingId}
      className={cn("scroll-mt-16 border-t border-border", className)}
    >
      <div
        className={cn(
          "mx-auto w-full max-w-6xl px-4 py-20 sm:px-6 sm:py-28",
          split && "lg:grid lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)] lg:gap-16",
        )}
      >
        <div>
          <p className="font-mono text-xs tracking-wide text-muted-foreground">
            <span aria-hidden="true" className="text-signal">
              {"// "}
            </span>
            {eyebrow}
          </p>
          <h2
            id={headingId}
            className="mt-3 max-w-3xl text-3xl font-semibold tracking-tight text-balance sm:text-4xl"
          >
            {title}
          </h2>
          {intro ? (
            <p className="mt-4 max-w-2xl text-base text-pretty text-muted-foreground sm:text-lg">
              {intro}
            </p>
          ) : null}
        </div>
        <div className={cn("mt-12", split && "lg:mt-0")}>{children}</div>
      </div>
    </section>
  );
}
