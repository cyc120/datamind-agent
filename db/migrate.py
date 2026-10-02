"""Apply checked-in idempotent metadata migrations to existing and fresh databases."""
from pathlib import Path
import os
import psycopg2

ROOT = Path(__file__).resolve().parents[1]


def migrate():
    dsn = os.environ.get("DATAMIND_WRITE_DSN") or "host=127.0.0.1 port=5432 dbname=datamind_meta user=datamind_app password=datamind_app_dev"
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(714202610)")
        for path in sorted((ROOT / "db/migrations").glob("*.sql")):
            cur.execute(path.read_text(encoding="utf-8"))
            print("applied", path.name)


if __name__ == "__main__":
    migrate()
