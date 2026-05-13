import { neon } from "@neondatabase/serverless";

if (!process.env.DATABASE_URL) {
  throw new Error("DATABASE_URL is not set");
}

// Tagged-template SQL helper. Use as: const rows = await sql`select 1 as x`;
export const sql = neon(process.env.DATABASE_URL);
