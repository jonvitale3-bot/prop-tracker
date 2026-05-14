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

export async function getTonightsPlays(
  minMentions: number = 1,
  includeKeyword: boolean = false,
): Promise<PlayRow[]> {
  // Default: only show plays supported by at least one handle_scrape mention.
  // includeKeyword=true brings in the legacy keyword-search data.
  // subject_kind='player' filters out moneyline / spread / total team bets.
  // mention_count is recomputed from the filtered mention set.
  const today = todayISO();
  const rows = includeKeyword
    ? ((await sql`
        SELECT p.id, p.sport, p.game_date::text AS game_date, p.subject, p.subject_kind,
               p.market, p.line::float8 AS line, p.side,
               COUNT(DISTINCT pm.mention_id)::int AS mention_count,
               p.public_pct::float8 AS public_pct,
               p.avg_conviction::float8 AS avg_conviction
        FROM plays p
        JOIN play_mentions pm ON pm.play_id = p.id
        WHERE p.game_date = ${today}
          AND p.subject_kind = 'player'
        GROUP BY p.id
        HAVING COUNT(DISTINCT pm.mention_id) >= ${minMentions}
        ORDER BY mention_count DESC, p.avg_conviction DESC NULLS LAST, p.id DESC
        LIMIT 100
      `) as unknown as PlayRow[])
    : ((await sql`
        SELECT p.id, p.sport, p.game_date::text AS game_date, p.subject, p.subject_kind,
               p.market, p.line::float8 AS line, p.side,
               COUNT(DISTINCT pm.mention_id)::int AS mention_count,
               p.public_pct::float8 AS public_pct,
               p.avg_conviction::float8 AS avg_conviction
        FROM plays p
        JOIN play_mentions pm ON pm.play_id = p.id
        JOIN mentions m ON m.id = pm.mention_id
        WHERE p.game_date = ${today}
          AND p.subject_kind = 'player'
          AND m.source_method = 'handle_scrape'
        GROUP BY p.id
        HAVING COUNT(DISTINCT pm.mention_id) >= ${minMentions}
        ORDER BY mention_count DESC, p.avg_conviction DESC NULLS LAST, p.id DESC
        LIMIT 100
      `) as unknown as PlayRow[]);
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
export async function getHotSubjects(
  minMentions: number = 1,
  includeKeyword: boolean = false,
): Promise<HotSubject[]> {
  // Player props only. Default to handle_scrape data; toggle via includeKeyword.
  const today = todayISO();
  const rows = includeKeyword
    ? ((await sql`
        SELECT p.sport, p.subject, p.market, p.side,
               COUNT(DISTINCT pm.mention_id)::int AS total_mentions,
               COUNT(DISTINCT p.line)::int AS distinct_lines,
               MIN(p.line)::float8 AS min_line,
               MAX(p.line)::float8 AS max_line,
               AVG(p.avg_conviction)::float8 AS avg_conviction
        FROM plays p
        JOIN play_mentions pm ON pm.play_id = p.id
        WHERE p.game_date = ${today}
          AND p.subject_kind = 'player'
        GROUP BY p.sport, p.subject, p.market, p.side
        HAVING COUNT(DISTINCT pm.mention_id) >= ${minMentions}
        ORDER BY total_mentions DESC, avg_conviction DESC NULLS LAST
        LIMIT 50
      `) as unknown as HotSubject[])
    : ((await sql`
        SELECT p.sport, p.subject, p.market, p.side,
               COUNT(DISTINCT pm.mention_id)::int AS total_mentions,
               COUNT(DISTINCT p.line)::int AS distinct_lines,
               MIN(p.line)::float8 AS min_line,
               MAX(p.line)::float8 AS max_line,
               AVG(p.avg_conviction)::float8 AS avg_conviction
        FROM plays p
        JOIN play_mentions pm ON pm.play_id = p.id
        JOIN mentions m ON m.id = pm.mention_id
        WHERE p.game_date = ${today}
          AND p.subject_kind = 'player'
          AND m.source_method = 'handle_scrape'
        GROUP BY p.sport, p.subject, p.market, p.side
        HAVING COUNT(DISTINCT pm.mention_id) >= ${minMentions}
        ORDER BY total_mentions DESC, avg_conviction DESC NULLS LAST
        LIMIT 50
      `) as unknown as HotSubject[]);
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
