# shared

Cross-cutting artifacts shared between `worker/` and `web/`.

- `sql/` — versioned SQL migration files. The worker applies them with
  `uv run migrate`. They are the **source of truth** for the database schema.

Files are named `NNN_description.sql` (e.g., `001_init.sql`). Each migration
is applied once and is recorded in a `_migrations` table.
