"""Load a validated snapshot into PostgreSQL's isolated place_staging schema."""
import argparse
import os
from pathlib import Path

from stadium_snapshot import SOURCE_RUN, validate

ROOT = Path(__file__).resolve().parents[2]


def insert_rows(cursor, table, columns, rows):
    """Bounded, parameterized multi-row INSERT without requiring COPY support."""
    from psycopg import sql

    columns = columns.split()
    for start in range(0, len(rows), 500):
        batch = rows[start:start + 500]
        placeholders = sql.SQL("({})").format(sql.SQL(",").join(sql.Placeholder() for _ in columns))
        statement = sql.SQL("INSERT INTO {} ({}) VALUES {}").format(
            sql.Identifier("place_staging", table),
            sql.SQL(",").join(map(sql.Identifier, columns)),
            sql.SQL(",").join(placeholders for _ in batch))
        cursor.execute(statement, [value for row in batch for value in row])


def load(connection, manifest, records, manifest_hash):
    from psycopg.types.json import Jsonb

    snapshot_id = manifest["snapshot_id"]
    # DDL and the complete snapshot commit together, or all roll back.
    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(20260927, 2500)")
            cursor.execute(Path(__file__).with_name("stadium_staging.sql").read_text(encoding="utf-8"))
            cursor.execute("SELECT manifest_sha256 FROM place_staging.snapshots WHERE snapshot_id = %s", (snapshot_id,))
            existing = cursor.fetchone()
            if existing:
                if existing[0] != manifest_hash:
                    raise ValueError("Snapshot ID already exists with different content")
                verify_counts(cursor, snapshot_id, manifest)
                return "already_loaded"
            cursor.execute("INSERT INTO place_staging.snapshots (snapshot_id, manifest_sha256, manifest) VALUES (%s, %s, %s)",
                           (snapshot_id, manifest_hash, Jsonb(manifest)))
            insert_rows(cursor, "source_runs", "snapshot_id stadium_code source collected_at reference_month metadata",
                [(snapshot_id, code, source, meta["completed_at"], meta.get("reference_month"), Jsonb(meta))
                 for code, stadium in manifest["stadiums"].items() for source, meta in stadium["sources"].items()])
            for code in manifest["stadiums"]:
                for filename, status in (("public_places.jsonl", "selected"), ("convenience_review.jsonl", "needs_review")):
                    rows = records[f"{code}/{filename}"]
                    insert_rows(cursor, "public_places", "snapshot_id stadium_code source source_run source_id selection_status "
                                "name address kind lat lng distance_m category_large category_middle category_small payload",
                            [(snapshot_id, code, row["source"], SOURCE_RUN[row["source"]], row["source_id"], status,
                              row["name"], row["address"], row["kind"], row["lat"], row["lng"], row["distance_m"],
                              row.get("category_large"), row.get("category_middle"), row.get("category_small"), Jsonb(row))
                             for row in rows])
                insert_rows(cursor, "google_lodging_ids", "snapshot_id stadium_code place_id",
                    [(snapshot_id, code, row["place_id"]) for row in records[f"{code}/google_lodging_ids.jsonl"]])
            verify_counts(cursor, snapshot_id, manifest)
    return "loaded"


def verify_counts(cursor, snapshot_id, manifest):
    # Detect a partial/manual load instead of incorrectly reporting a successful no-op.
    for code in manifest["stadiums"]:
        for filename, status in (("public_places.jsonl", "selected"), ("convenience_review.jsonl", "needs_review")):
            cursor.execute("SELECT count(*) FROM place_staging.public_places WHERE snapshot_id=%s AND stadium_code=%s AND selection_status=%s",
                           (snapshot_id, code, status))
            if cursor.fetchone()[0] != manifest["files"][f"{code}/{filename}"]["rows"]:
                raise ValueError("Loaded public row count mismatch")
        cursor.execute("SELECT count(*) FROM place_staging.google_lodging_ids WHERE snapshot_id=%s AND stadium_code=%s", (snapshot_id, code))
        if cursor.fetchone()[0] != manifest["files"][f"{code}/google_lodging_ids.jsonl"]["rows"]:
            raise ValueError("Loaded Google row count mismatch")
    cursor.execute("SELECT count(*) FROM place_staging.source_runs WHERE snapshot_id=%s", (snapshot_id,))
    if cursor.fetchone()[0] != len(manifest["stadiums"]) * 4:
        raise ValueError("Loaded source run count mismatch")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--apply", action="store_true", help="Create staging tables and commit the import; default only validates files")
    parser.add_argument("--host", help="Override DB_HOST, e.g. localhost for a published Docker port")
    args = parser.parse_args()
    try:
        manifest, records, manifest_hash = validate(args.snapshot)
        if not args.apply:
            print(f"validated {manifest['snapshot_id']}; no database writes")
            return
        import psycopg
        from dotenv import load_dotenv
        load_dotenv(ROOT / "backend/.env", override=False)
        load_dotenv(ROOT / ".env", override=False)
        # Match Django's env precedence; never log a DSN/password or raw DB exception.
        with psycopg.connect(dbname=os.getenv("DB_NAME", "mydb"), user=os.getenv("DB_USER", "myuser"),
                             password=os.getenv("DB_PASSWORD", ""), host=args.host or os.getenv("DB_HOST", "db"),
                             port=os.getenv("DB_PORT", "5432"), connect_timeout=5, autocommit=True) as connection:
            result = load(connection, manifest, records, manifest_hash)
        print(f"{result} {manifest['snapshot_id']}: {manifest['totals']}")
    except ImportError:
        parser.exit(1, "Install backend dependencies (psycopg[binary], python-dotenv) to load PostgreSQL.\n")
    except (ValueError, KeyError, TypeError, OSError):
        parser.exit(1, "Import stopped: check snapshot integrity, configuration and existing snapshot counts.\n")
    except Exception:
        parser.exit(1, "PostgreSQL import failed and was rolled back. Check DB availability, credentials and CREATE schema permissions.\n")


if __name__ == "__main__":
    main()
