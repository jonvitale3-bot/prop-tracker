import type { Stats } from "@/lib/queries";

function winPct(w: number, l: number): string {
  const total = w + l;
  if (total === 0) return "—";
  return `${((w / total) * 100).toFixed(1)}%`;
}

function Box({ title, w, l, sub }: { title: string; w: number; l: number; sub?: string }) {
  return (
    <div className="rounded-lg border border-neutral-800 bg-neutral-950 px-4 py-3">
      <div className="text-xs uppercase tracking-wide text-neutral-500">{title}</div>
      <div className="mt-1 flex items-baseline gap-2">
        <span className="text-2xl font-semibold tabular-nums">
          {w}-{l}
        </span>
        <span className="text-sm text-neutral-400 tabular-nums">{winPct(w, l)}</span>
      </div>
      {sub && <div className="mt-1 text-xs text-neutral-500">{sub}</div>}
    </div>
  );
}

export function StatStrip({ allTime, last7 }: { allTime: Stats; last7: Stats }) {
  const sub = (s: Stats) =>
    `${s.total_graded} graded · ${s.pushes} push · ${s.voided} void`;
  return (
    <section className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      <Box title="Public — All time" w={allTime.public_wins} l={allTime.public_losses} sub={sub(allTime)} />
      <Box title="Fade — All time"   w={allTime.fade_wins}   l={allTime.fade_losses}   sub={sub(allTime)} />
      <Box title="Public — Last 7d"  w={last7.public_wins}   l={last7.public_losses}   sub={sub(last7)} />
      <Box title="Fade — Last 7d"    w={last7.fade_wins}     l={last7.fade_losses}     sub={sub(last7)} />
    </section>
  );
}
