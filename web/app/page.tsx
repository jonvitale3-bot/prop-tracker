import { PlaysTable } from "@/components/PlaysTable";
import { StatStrip } from "@/components/StatStrip";
import { getStats, getTonightsPlays, todayET } from "@/lib/queries";

// Revalidate every 60 seconds so the dashboard stays fresh without
// requiring a hard refresh.
export const revalidate = 60;

export default async function Home() {
  const [plays, allTime, last7] = await Promise.all([
    getTonightsPlays(),
    getStats(null),
    getStats(7),
  ]);

  const today = todayET();

  return (
    <main className="mx-auto max-w-5xl p-6 sm:p-8">
      <header className="mb-6 flex items-baseline justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">prop-tracker</h1>
          <p className="mt-1 text-sm text-neutral-400">
            Public sentiment vs. results · {today}
          </p>
        </div>
        <a
          href="https://github.com/jonvitale3-bot/prop-tracker"
          target="_blank"
          rel="noreferrer"
          className="text-xs text-neutral-500 hover:text-neutral-300"
        >
          source
        </a>
      </header>

      <StatStrip allTime={allTime} last7={last7} />

      <section className="mt-8">
        <h2 className="mb-3 text-sm font-medium uppercase tracking-wide text-neutral-400">
          Tonight&apos;s plays ({plays.length})
        </h2>
        <PlaysTable rows={plays} />
      </section>
    </main>
  );
}
