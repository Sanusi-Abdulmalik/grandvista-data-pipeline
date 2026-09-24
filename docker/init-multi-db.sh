#!/bin/bash
# Runs once, on first container start, via Postgres's docker-entrypoint-initdb.d
# mechanism. Creates the grandvista role + database (used by the pipeline's
# raw/warehouse schemas) in addition to the `airflow` db created automatically
# from POSTGRES_DB.

set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE USER ${GRANDVISTA_USER} WITH PASSWORD '${GRANDVISTA_PASSWORD}';
    CREATE DATABASE ${GRANDVISTA_DB} OWNER ${GRANDVISTA_USER};
    GRANT ALL PRIVILEGES ON DATABASE ${GRANDVISTA_DB} TO ${GRANDVISTA_USER};
EOSQL

echo "Created database '${GRANDVISTA_DB}' owned by '${GRANDVISTA_USER}'."

# The warehouse/raw schema DDL (sql/schema/schema.sql) is mounted alongside
# this script but must run against the newly created grandvista database,
# not the default one this init script itself runs against — so apply it
# explicitly here rather than relying on docker-entrypoint-initdb.d's default
# "run every *.sql against POSTGRES_DB" behavior.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$GRANDVISTA_DB" -f /docker-entrypoint-initdb.d/schema.sql.template

echo "Applied schema.sql to '${GRANDVISTA_DB}'."
