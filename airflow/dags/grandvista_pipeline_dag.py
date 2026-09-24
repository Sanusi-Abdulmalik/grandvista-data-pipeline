"""
grandvista_pipeline_dag.py

Orchestrates the full GrandVista hotel booking pipeline:

    generate_synthetic_data
        -> ingest_raw_data
        -> transform_and_load
        -> run_data_quality_checks
        -> run_dbt (deps -> run -> test)

Each stage is a separate task so failures are isolated and visible in
the Airflow UI, and so any single stage can be retried without
re-running the whole pipeline. run_data_quality_checks is a hard gate:
if it fails, the DAG does not proceed to dbt, and a failure is
surfaced rather than silently loading bad data.
"""

from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.python import PythonOperator

PROJECT_ROOT = "/opt/airflow/project"  # mounted into the Airflow container, see docker-compose.yml

default_args = {
    "owner": "data-engineering",
    "retries": 2,
    "retry_delay": timedelta(minutes=3),
    "email_on_failure": False,
}

with DAG(
    dag_id="grandvista_hotel_booking_pipeline",
    description="Generate, ingest, transform, validate, and warehouse GrandVista hotel booking data",
    default_args=default_args,
    schedule="0 2 * * *",  # daily at 02:00
    start_date=datetime(2026, 1, 1),
    catchup=False,
    max_active_runs=1,
    tags=["grandvista", "etl", "hospitality"],
) as dag:

    generate_synthetic_data = BashOperator(
        task_id="generate_synthetic_data",
        bash_command=(
            f"cd {PROJECT_ROOT}/data/generation && "
            f"python generate_data.py --out-dir {PROJECT_ROOT}/data/raw"
        ),
    )

    ingest_raw_data = BashOperator(
        task_id="ingest_raw_data",
        bash_command=(
            f"cd {PROJECT_ROOT} && "
            f"python src/ingestion/ingest.py --raw-dir data/raw"
        ),
    )

    transform_and_load = BashOperator(
        task_id="transform_and_load",
        bash_command=(
            f"cd {PROJECT_ROOT} && "
            f"python src/transformation/load.py --raw-dir data/raw"
        ),
    )

    run_data_quality_checks = BashOperator(
        task_id="run_data_quality_checks",
        bash_command=(
            f"cd {PROJECT_ROOT} && "
            f"python src/validation/validate.py"
        ),
    )

    dbt_deps = BashOperator(
        task_id="dbt_deps",
        bash_command=f"cd {PROJECT_ROOT}/dbt && dbt deps --profiles-dir .",
    )

    dbt_run = BashOperator(
        task_id="dbt_run",
        bash_command=f"cd {PROJECT_ROOT}/dbt && dbt run --profiles-dir .",
    )

    dbt_test = BashOperator(
        task_id="dbt_test",
        bash_command=f"cd {PROJECT_ROOT}/dbt && dbt test --profiles-dir .",
    )

    (
        generate_synthetic_data
        >> ingest_raw_data
        >> transform_and_load
        >> run_data_quality_checks
        >> dbt_deps
        >> dbt_run
        >> dbt_test
    )
