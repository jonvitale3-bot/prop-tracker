# web

Next.js dashboard for prop-tracker.

## Setup

```bash
cd web
npm install
cp .env.example .env.local   # then fill in the pooled Neon DATABASE_URL
npm run dev                  # http://localhost:3000
```

## Notes

- Uses the **App Router** and **Tailwind**.
- Database access uses `@neondatabase/serverless` (HTTP fetch driver, works
  on Vercel's edge runtime). Helper is in `lib/db.ts`.
