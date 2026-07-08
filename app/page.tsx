import { Suspense } from "react";
import { HotSubjects } from "@/components/HotSubjects";
import { PlaysTable } from "@/components/PlaysTable";
import { SectionSkeleton, StatStripSkeleton } from "@/components/Skeletons";
import { StatStrip } from "@/components/StatStrip";
import { getHotSubjects, getStats, getTonightsPlays, todayET } from "@/lib/queries";

// Revalidate every 60 seconds so the dashboard stays fresh without
// requiring a hard refresh.
export const revalidate = 60;

// Section copy is shared between the loaded section and its skeleton fallback
// so the two stay in sync and the layout doesn't shift when data streams in.
const HOT_DESC =
  "Player props grouped by player + market + side (ignoring line). Sorted by total mentions. A mention count of 1 is one poster's lean; 3+ starts to look like consensus.";
const PLAYS_DESC =
  "Strict line match. Player props only. Lower mention count = sparser data; the column tells the truth.";

// Each section fetches its own data and is streamed in via <Suspense>. Neon
// suspends its compute after a few minutes idle, so the first query after the
// site has been idle has to wake it (this is the "30-40s after idle" cold
// start). Fetching inside Suspense boundaries lets the header/shell paint
// immediately and each section fill in when its query resolves, rather than
// blocking the whole page render on a cold database.

async function StatsSection() {
  const [allTime, last7] = await Promise.all([getStats(null), getStats(7)]);
  return <StatStrip allTime={allTime} last7={last7} />;
}

async function HotSection({ includeKeyword }: { includeKeyword: boolean }) {
  const hot = await getHotSubjects(1, includeKeyword);
  return (
    <>
      <h2 className="mb-1 text-sm font-medium uppercase tracking-wide text-neutral-400">
        Hot players ({hot.length})
      </h2>
      <p className="mb-3 text-xs text-neutral-500">{HOT_DESC}</p>
      <HotSubjects rows={hot} />
    </>
  );
}

async function PlaysSection({ includeKeyword }: { includeKeyword: boolean }) {
  const plays = await getTonightsPlays(1, includeKeyword);
  return (
    <>
      <h2 className="mb-1 text-sm font-medium uppercase tracking-wide text-neutral-400">
        Tonight&apos;s exact player props ({plays.length})
      </h2>
      <p className="mb-3 text-xs text-neutral-500">{PLAYS_DESC}</p>
      <PlaysTable rows={plays} />
    </>
  );
}

export default async function Home(
  { searchParams }: { searchParams: Promise<{ includeKeyword?: string }> },
) {
  const params = await searchParams;
  const includeKeyword = params?.includeKeyword === "1";

  // Computed without touching the DB, so the shell renders instantly.
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
        <div className="flex items-center gap-4">
          <a
            href={includeKeyword ? "/" : "/?includeKeyword=1"}
            className="text-xs text-neutral-500 hover:text-neutral-300"
          >
            {includeKeyword ? "hide keyword data" : "include keyword data"}
          </a>
          <a
            href="https://github.com/jonvitale3-bot/prop-tracker"
            target="_blank"
            rel="noreferrer"
            className="text-xs text-neutral-500 hover:text-neutral-300"
          >
            source
          </a>
        </div>
      </header>

      <Suspense fallback={<StatStripSkeleton />}>
        <StatsSection />
      </Suspense>

      <section className="mt-8">
        <Suspense
          fallback={<SectionSkeleton title="Hot players" desc={HOT_DESC} />}
        >
          <HotSection includeKeyword={includeKeyword} />
        </Suspense>
      </section>

      <section className="mt-8">
        <Suspense
          fallback={
            <SectionSkeleton title="Tonight's exact player props" desc={PLAYS_DESC} />
          }
        >
          <PlaysSection includeKeyword={includeKeyword} />
        </Suspense>
      </section>
    </main>
  );
}
