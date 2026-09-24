import argparse
import json
import logging
import math
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

sys.path.append(str(Path(__file__).resolve().parent.parent.parent))
from src.transformation.transform import run_transformations  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")
logger = logging.getLogger("load")


def get_engine():
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    db = os.getenv("POSTGRES_DB", "grandvista")
    user = os.getenv("POSTGRES_USER", "grandvista")
    password = os.getenv("POSTGRES_PASSWORD", "grandvista")
    url = f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{db}"
    return create_engine(url)


def _jsonable(obj):
    """Recursively replace values that aren't valid JSON tokens (NaN, +/-Infinity)."""
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    return obj


def load_dataframe(engine, df, table_name: str):
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE TABLE warehouse.{table_name} CASCADE"))
    df.to_sql(table_name, engine, schema="warehouse", if_exists="append", index=False, method="multi", chunksize=1000)
    logger.info(f"Loaded {len(df):,} rows into warehouse.{table_name}")


def load_rejected(engine, rejected: list):
    if not rejected:
        return
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE warehouse.rejected_records;"))
        for r in rejected:
            conn.execute(
                text(
                    "INSERT INTO warehouse.rejected_records (source_table, raw_record, rejection_reason) "
                    "VALUES (:source_table, :raw_record, :reason)"
                ),
                {
                    "source_table": r.source_table,
                    "raw_record": json.dumps(_jsonable(r.raw_record), default=str, allow_nan=False),
                    "reason": r.reason,
                },
            )
    logger.info(f"Logged {len(rejected):,} rejected records to warehouse.rejected_records")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-dir", type=str, default="data/raw")
    args = parser.parse_args()

    result = run_transformations(Path(args.raw_dir))
    engine = get_engine()

    # Load order matters: parents before children.
    load_dataframe(engine, result.properties, "properties")
    load_dataframe(engine, result.rooms, "rooms")
    load_dataframe(engine, result.guests, "guests")
    load_dataframe(engine, result.reservations, "reservations")
    load_dataframe(engine, result.payments, "payments")
    load_rejected(engine, result.rejected)

    logger.info("Load complete.")


if __name__ == "__main__":
    main()