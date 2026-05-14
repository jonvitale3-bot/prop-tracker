import type { HotSubject } from "@/lib/queries";

function fmtLineRange(min: number | null, max: number | null, count: number): string {
  if (min === null || max === null) return "—";
  if (count === 1 || min === max) return min.toString();
  return `${min}–${max} (${count} variants)`;
}

export function HotSubjects({ rows }: { rows: HotSubject[] }) {
  if (rows.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-neutral-800 px-4 py-6 text-center text-sm text-neutral-500">
        No consensus subjects yet — needs 3+ mentions on the same player/market/side.
      </div>
    );
  }
  return (
    <div className="overflow-x-auto rounded-lg border border-neutral-800">
      <table className="w-full text-sm">
        <thead className="bg-neutral-950 text-xs uppercase tracking-wide text-neutral-500">
          <tr>
            <th className="px-3 py-2 text-left">Subject</th>
            <th className="px-3 py-2 text-left">Market</th>
            <th className="px-3 py-2 text-left">Side</th>
            <th className="px-3 py-2 text-right">Lines</th>
            <th className="px-3 py-2 text-right">Mentions</th>
            <th className="px-3 py-2 text-right">Conv</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-neutral-900">
          {rows.map((r, i) => (
            <tr key={`${r.subject}-${r.market}-${r.side}-${i}`} className="hover:bg-neutral-950">
              <td className="px-3 py-2 font-medium">
                {r.subject}{" "}
                <span className="ml-1 text-xs text-neutral-500">[{r.sport}]</span>
              </td>
              <td className="px-3 py-2 text-neutral-300">{r.market}</td>
              <td className="px-3 py-2">
                <span className="rounded bg-neutral-900 px-1.5 py-0.5 text-xs uppercase">
                  {r.side}
                </span>
              </td>
              <td className="px-3 py-2 text-right tabular-nums">
                {fmtLineRange(r.min_line, r.max_line, r.distinct_lines)}
              </td>
              <td className="px-3 py-2 text-right tabular-nums font-medium">
                {r.total_mentions}
              </td>
              <td className="px-3 py-2 text-right tabular-nums">
                {r.avg_conviction === null ? "—" : r.avg_conviction.toFixed(1)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
