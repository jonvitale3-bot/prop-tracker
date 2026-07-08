import { sql } from "@/lib/db";

// Keep-warm endpoint. Neon's compute suspends after a few minutes of
// inactivity (scale-to-zero); the first query after that has to resume it,
// which is the source of the "slow after idle" load. Ping this route on a
// short interval (e.g. every 4 minutes from an uptime pinger or cron) so the
// database compute never gets a chance to suspend during your active hours.
//
// It runs a trivial `SELECT 1` and reports how long the round trip took, so
// hitting it in a browser also tells you whether the DB is currently warm
// (single-digit ms) or just woke up (hundreds of ms to seconds).

export const dynamic = "force-dynamic";

export async function GET() {
  const start = Date.now();
  try {
    await sql`SELECT 1`;
    return Response.json(
      { ok: true, db_ms: Date.now() - start },
      { headers: { "Cache-Control": "no-store" } },
    );
  } catch (err) {
    return Response.json(
      { ok: false, db_ms: Date.now() - start, error: String(err) },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    );
  }
}
