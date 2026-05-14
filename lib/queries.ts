import { sql } from "./db";

export type PlayRow = {
  id: number;
  sport: string;
  game_date: string;
  subject: string;
  subject_kind: string;
  market: string;
  line: number | null;
  side: string;
  mention_count: number;
  public_pct: number | null;
  avg_conviction: number | null;
};

export type Stats = {
  public_wins: number;
  public_losses: number;
  fade_wins: number;
  fade_losses: number;
  pushes: number;
  voided: number;
  total_graded: number;
};

// NBA game dates are scheduled in US Eastern. Server may be UTC (Vercel)
// so we explicitly format "today" in America/New_York so the dashboard's
// notion of "tonight" matches when games actually tip off.
export function todayET(): string {
  const fmt = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/New_York",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
  // en-CA with 2-digit fields produces "YYYY-MM-DD" already.
  return fmt.format(new Date());
}
const todayISO = todayET;

export async function getTonightsPlays(minMentions: number = 2): Promise<PlayRow[]> {
  // minMentions filter drops "lone wolf" picks (single tweets). public_pct is
  // meaningless until at least a couple people weigh in on the same bet.
  const today = todayISO();
  const rows = (await sql`
    SELECT id, sport, game_date::text AS game_date, subject, subject_kind,
           market, line::float8 AS line, side, mention_count,
           public_pct::float8 AS public_pct, avg_conviction::float8 AS avg_conviction
    FROM plays
    WHERE game_date = ${today}
      AND mention_count >= ${minMentions}
    ORDER BY mention_count DESC, avg_conviction DESC NULLS LAST, id DESC
    LIMIT 100
  `) as unknown as PlayRow[];
  return rows;
}

export type HotSubject = {
  sport: string;
  subject: string;
  market: string;
  side: string;
  total_mentions: number;
  distinct_lines: number;
  min_line: number | null;
  max_line: number | null;
  avg_conviction: number | null;
};

/**
 * Looser aggregation: ignore the specific line so "Tatum o27.5" and "Tatum o28.5"
 * get combined. Surfaces consensus on a *direction* (subject + market + side)
 * even when posters disagree on the exact number.
 */
export async function getHotSubjects(minMentions: number = 3): Promise<HotSubject[]> {
  const today = todayISO();
  const rows = (await sql`
    SELECT sport, subject, market, side,
           SUM(mention_count)::int AS total_mentions,
           COUNT(DISTINCT line)::int AS distinct_lines,
           MIN(line)::float8 AS min_line,
           MAX(line)::float8 AS max_line,
           AVG(avg_conviction)::float8 AS avg_conviction
    FROM plays
    WHERE game_date = ${today}
    GROUP BY sport, subject, market, side
    HAVING SUM(mention_count) >= ${minMentions}
    ORDER BY total_mentions DESC, avg_conviction DESC NULLS LAST
    LIMIT 50
  `) as unknown as HotSubject[];
  return rows;
}

export async function getStats(windowDays: number | null = null): Promise<Stats> {
  // Build a WHERE clause for the time window
  const rows = windowDays
    ? ((await sql`
        SELECT
          SUM(CASE WHEN public_won AND NOT push THEN 1 ELSE 0 END)::int AS public_wins,
          SUM(CASE WHEN NOT public_won AND NOT push THEN 1 ELSE 0 END)::int AS public_losses,
          SUM(CASE WHEN fade_won THEN 1 ELSE 0 END)::int AS fade_wins,
          SUM(CASE WHEN NOT fade_won AND NOT push THEN 1 ELSE 0 END)::int AS fade_losses,
          SUM(CASE WHEN push THEN 1 ELSE 0 END)::int AS pushes,
          SUM(CASE WHEN public_won IS NULL THEN 1 ELSE 0 END)::int AS voided,
          COUNT(*)::int AS total_graded
        FROM results r
        JOIN plays p ON p.id = r.play_id
        WHERE r.graded_at >= now() - (${windowDays}::text || ' days')::interval
      `) as unknown as Stats[])
    : ((await sql`
        SELECT
          SUM(CASE WHEN public_won AND NOT push THEN 1 ELSE 0 END)::int AS public_wins,
          SUM(CASE WHEN NOT public_won AND NOT push THEN 1 ELSE 0 END)::int AS public_losses,
          SUM(CASE WHEN fade_won THEN 1 ELSE 0 END)::int AS fade_wins,
          SUM(CASE WHEN NOT fade_won AND NOT push THEN 1 ELSE 0 END)::int AS fade_losses,
          SUM(CASE WHEN push THEN 1 ELSE 0 END)::int AS pushes,
          SUM(CASE WHEN public_won IS NULL THEN 1 ELSE 0 END)::int AS voided,
          COUNT(*)::int AS total_graded
        FROM results
      `) as unknown as Stats[]);
  const r = rows[0] ?? { public_wins: 0, public_losses: 0, fade_wins: 0, fade_losses: 0, pushes: 0, voided: 0, total_graded: 0 };
  return {
    public_wins: r.public_wins ?? 0,
    public_losses: r.public_losses ?? 0,
    fade_wins: r.fade_wins ?? 0,
    fade_losses: r.fade_losses ?? 0,
    pushes: r.pushes ?? 0,
    voided: r.voided ?? 0,
    total_graded: r.total_graded ?? 0,
  };
}
