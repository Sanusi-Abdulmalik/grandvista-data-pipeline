"""
ingest.py

Data Ingestion stage.

Reads the raw source CSVs (data/raw/*.csv) and loads them, unmodified,
into the `raw` Postgres schema. This preserves an exact copy of the
source data (including bad values, mixed formats, blanks) before any
cleaning happens, so the pipeline can always be re-run from a stable
starting point and so failures can be traced back to what the source
system actually sent.

Usage:
    python ingest.py --raw-dir data/raw

Environment variables (see .env.example):
    POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD
"""

import argparse
import logging
import os
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("ingest")

TABLES = ["guests", "properties", "rooms", "reservations", "payments"]


def get_engine():
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    db = os.getenv("POSTGRES_DB", "grandvista")
    user = os.getenv("POSTGRES_USER", "grandvista")
    password = os.getenv("POSTGRES_PASSWORD", "grandvista")
    url = f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{db}"
    return create_engine(url)


def ingest_table(engine, raw_dir: Path, table_name: str) -> int:
    csv_path = raw_dir / f"{table_name}.csv"
    if not csv_path.exists():
        raise FileNotFoundError(f"Expected source file not found: {csv_path}")

    logger.info(f"Reading {csv_path} ...")
    # dtype=str keeps the raw layer purely textual — no implicit type
    # coercion or precision loss before the transformation stage runs.
    df = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
    df["_source_file"] = csv_path.name

    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE TABLE raw.{table_name}"))

    df.to_sql(
        table_name,
        engine,
        schema="raw",
        if_exists="append",
        index=False,
        method="multi",
        chunksize=1000,
    )
    logger.info(f"Loaded {len(df):,} rows into raw.{table_name}")
    return len(df)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-dir", type=str, default="data/raw")
    args = parser.parse_args()

    raw_dir = Path(args.raw_dir)
    engine = get_engine()

    total = 0
    try:
        for table in TABLES:
            total += ingest_table(engine, raw_dir, table)
    except Exception:
        logger.exception("Ingestion failed")
        sys.exit(1)

    logger.info(f"Ingestion complete. {total:,} total rows loaded across {len(TABLES)} tables.")


if __name__ == "__main__":
    main()
