// Loading fallbacks for the streamed dashboard sections. The DB (Neon)
// suspends its compute after a few minutes idle, so the first query after
// the site has been idle has to wake it. These skeletons let the page shell
// paint immediately and each section show its structure while its query
// resolves, instead of a blank screen blocking on a cold database.

function Pulse({ className = "" }: { className?: string }) {
  return <div className={`animate-pulse rounded bg-neutral-900 ${className}`} />;
}

export function StatStripSkeleton() {
  return (
    <section className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      {Array.from({ length: 4 }).map((_, i) => (
        <div
          key={i}
          className="rounded-lg border border-neutral-800 bg-neutral-950 px-4 py-3"
        >
          <Pulse className="h-3 w-20" />
          <Pulse className="mt-2 h-7 w-24" />
          <Pulse className="mt-2 h-3 w-28" />
        </div>
      ))}
    </section>
  );
}

function TableSkeleton({ rows = 5 }: { rows?: number }) {
  return (
    <div className="overflow-hidden rounded-lg border border-neutral-800">
      <div className="border-b border-neutral-800 bg-neutral-950 px-3 py-2.5">
        <Pulse className="h-3 w-40" />
      </div>
      <div className="divide-y divide-neutral-900">
        {Array.from({ length: rows }).map((_, i) => (
          <div key={i} className="flex items-center justify-between px-3 py-3">
            <Pulse className="h-4 w-40" />
            <Pulse className="h-4 w-12" />
          </div>
        ))}
      </div>
    </div>
  );
}

/**
 * Fallback for a labeled table section. Mirrors the heading + description +
 * table layout of the loaded section so the page doesn't visually jump when
 * the data streams in.
 */
export function SectionSkeleton({
  title,
  desc,
  rows = 5,
}: {
  title: string;
  desc: string;
  rows?: number;
}) {
  return (
    <>
      <h2 className="mb-1 text-sm font-medium uppercase tracking-wide text-neutral-400">
        {title}
      </h2>
      <p className="mb-3 text-xs text-neutral-500">{desc}</p>
      <TableSkeleton rows={rows} />
    </>
  );
}
