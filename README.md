# prop-tracker

A personal sports-betting analysis tool. Aggregates public betting sentiment from
Reddit and Twitter, uses Claude to parse the raw posts into structured plays,
then grades the outcomes to see whether **fading the public** is profitable.

> Personal use, not financial advice, etc.

## Stack

| Component   | Tech                                                    |
| ----------- | ------------------------------------------------------- |
| Worker      | Python 3.11 + `uv`, raw `psycopg`, deployed to Railway |
| Dashboard   | Next.js 15 (App Router) + TypeScript, deployed to Vercel |
| Database    | Neon Postgres (shared)                                  |
| Scraping    | Apify actors (Reddit + Twitter)                         |
| Parsing     | Anthropic Claude Haiku                                  |
| Box scores  | ESPN unofficial JSON API                                |

## Repo layout

```
prop-tracker/
├── app/, components/, lib/    # Next.js dashboard (root, deployed to Vercel)
├── package.json, tsconfig.json, next.config.mjs, tailwind.config.ts
├── worker/                    # Python: scrapers + parser + grader (Railway)
├── shared/sql/                # Database migrations (source of truth)
├── Dockerfile, railway.json   # Worker deploy config
├── .env.example
└── README.md
```

## Sports & markets

- **NBA** first (playoffs), **MLB** next.
- Markets: player props, spreads, moneylines, totals (over/under).

## Data model (high-level)

- **mentions** — raw posts from Reddit/Twitter (source, author, ts, raw text, engagement).
- **plays** — parsed bets (player/team, market, line, side, conviction 1-5, public %).
- **results** — graded outcomes (closing line, actual stat, public won?, fade won?).

## Quickstart

See [worker/README.md](./worker/README.md) for the Python worker setup.
For the dashboard: `npm install && npm run dev` from this directory.
Start by copying `.env.example` to `.env` and filling in the secrets.
