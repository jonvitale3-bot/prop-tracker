import type { PlayRow } from "@/lib/queries";

function fmtLine(line: number | null): string {
  if (line === null || line === undefined) return "—";
  return Number.isInteger(line) ? line.toString() : line.toFixed(1);
}

function fmtPublicPct(p: number | null): string {
  if (p === null || p === undefined) return "—";
  return `${p.toFixed(0)}%`;
}

function fmtConviction(c: number | null): string {
  if (c === null || c === undefined) return "—";
  return c.toFixed(1);
}

export function PlaysTable({ rows }: { rows: PlayRow[] }) {
  if (rows.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-neutral-800 px-4 py-8 text-center text-sm text-neutral-500">
        No plays for tonight yet. Run <code className="rounded bg-neutral-900 px-1.5 py-0.5">ingest-reddit</code>{" "}
        then <code className="rounded bg-neutral-900 px-1.5 py-0.5">parse-mentions</code>.
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
            <th className="px-3 py-2 text-right">Line</th>
            <th className="px-3 py-2 text-left">Side</th>
            <th className="px-3 py-2 text-right">Public %</th>
            <th className="px-3 py-2 text-right">Conv</th>
            <th className="px-3 py-2 text-right">Mentions</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-neutral-900">
          {rows.map((r) => (
            <tr key={r.id} className="hover:bg-neutral-950">
              <td className="px-3 py-2 font-medium">
                {r.subject}{" "}
                <span className="ml-1 text-xs text-neutral-500">[{r.sport}]</span>
              </td>
              <td className="px-3 py-2 text-neutral-300">{r.market}</td>
              <td className="px-3 py-2 text-right tabular-nums">{fmtLine(r.line)}</td>
              <td className="px-3 py-2">
                <span className="rounded bg-neutral-900 px-1.5 py-0.5 text-xs uppercase">
                  {r.side}
                </span>
              </td>
              <td className="px-3 py-2 text-right tabular-nums">{fmtPublicPct(r.public_pct)}</td>
              <td className="px-3 py-2 text-right tabular-nums">{fmtConviction(r.avg_conviction)}</td>
              <td className="px-3 py-2 text-right tabular-nums">{r.mention_count}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
