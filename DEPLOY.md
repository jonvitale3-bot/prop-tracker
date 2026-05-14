# Deployment

The repo has two deploy targets and they each auto-deploy on every push to `main`.

## Vercel (dashboard)

1. Go to https://vercel.com/new and import the `prop-tracker` repo.
2. **Framework Preset:** Next.js (auto-detected).
3. **Root Directory:** `web`  ← important, the repo is a monorepo.
4. **Environment Variables** — add:
   - `DATABASE_URL` = your **pooled** Neon connection string (the one with `-pooler` in the host).
5. Click **Deploy**. Production URL appears in ~1 minute.
6. After deploy, the URL is on your project page; copy it and bookmark on your phone.

Future commits to `main` redeploy automatically. To re-trigger without a commit,
use the **Redeploy** button in the Vercel UI.

## Railway (worker)

The worker is one long-running service. It:

- pulls Reddit + parses every 30 min (`WORKER_CYCLE_SECONDS`)
- grades yesterday's plays on the first cycle after 4 AM ET (`WORKER_GRADE_HOUR_ET`)
- migrates the DB on every boot (idempotent)

### Setup

1. Go to https://railway.com/new and choose **Deploy from GitHub repo**, pick `prop-tracker`.
2. Railway detects `Dockerfile` and `railway.json` at the repo root.
3. **Environment Variables** — add these in the service's **Variables** tab:
   - `DATABASE_URL` (pooled Neon URL)
   - `DATABASE_URL_DIRECT` (non-pooled Neon URL)
   - `ANTHROPIC_API_KEY`
   - `APIFY_TOKEN`
   - `APIFY_REDDIT_ACTOR=trudax~reddit-scraper-lite`  *(optional, this is the default)*
   - `REDDIT_SUBREDDITS=sportsbook,nba,nbabetting,PlayerProps,NBABets`
   - `ODDS_API_KEY` *(optional for v1; only used by the closing-line backfill)*
   - `LOG_LEVEL=INFO`
4. Click **Deploy**. The first build takes a couple minutes.

### Verifying

- Open **Logs** in Railway. You should see:
  ```
  orchestrator: starting (cycle=1800s, grade_hour_ET=4)
  orchestrator: --- cycle start ---
  orchestrator: -> ingest-reddit
  Reddit ingest: subreddits=sportsbook,nba,... actor=...
  Apify: starting actor trudax~reddit-scraper-lite
  ...
  ```
- After the first cycle, refresh the Vercel dashboard. New plays should appear if
  any of the latest Reddit posts contained an explicit pick.

### Adjusting cadence

In Railway's **Variables** tab, set:

- `WORKER_CYCLE_SECONDS=900` for 15-minute cycles (doubles Apify cost).
- `WORKER_GRADE_HOUR_ET=3` to grade earlier.

## Cost (rough)

- **Neon**: free tier covers a personal use database easily.
- **Vercel**: free Hobby tier is plenty.
- **Railway**: $5/mo Hobby tier covers one always-on worker.
- **Apify**: free tier or pay-as-you-go. 30-min cadence × 100 posts/run ≈ $3-5/mo on the
  Reddit-scraper-lite actor.
- **Anthropic**: ~$0.01 per 1k mentions parsed with Haiku. Practically nothing.
- **ESPN**: free unofficial API.
- **The Odds API**: $30/mo on the user's existing subscription, only used for closing-line backfills.
