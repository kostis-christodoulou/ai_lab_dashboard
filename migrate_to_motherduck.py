from __future__ import annotations

import os

os.environ["SKIP_DB_INIT"] = "1"

import duckdb
import pandas as pd

import app3


def connect_motherduck_admin() -> duckdb.DuckDBPyConnection:
    token = app3.clean_text(os.getenv("MOTHERDUCK_TOKEN", ""))
    if not token:
        raise ValueError("MOTHERDUCK_TOKEN is required to migrate to MotherDuck.")

    database_name = app3.clean_text(
        os.getenv("MOTHERDUCK_DB", app3.DEFAULT_MOTHERDUCK_DB)
    ) or app3.DEFAULT_MOTHERDUCK_DB

    conn = duckdb.connect(f"md:?motherduck_token={token}")
    conn.execute(f"CREATE DATABASE IF NOT EXISTS {app3.quote_identifier(database_name)}")
    conn.execute(f"USE {app3.quote_identifier(database_name)}")
    return conn


def reset_remote_schema() -> None:
    with connect_motherduck_admin() as conn:
        conn.execute("DROP TABLE IF EXISTS tasks")
        conn.execute("DROP TABLE IF EXISTS interactions")
        conn.execute("DROP TABLE IF EXISTS projects")

    app3.initialize_database(app3.PROJECTS_DF)


def copy_table(
    source: duckdb.DuckDBPyConnection,
    target: duckdb.DuckDBPyConnection,
    table_name: str,
    columns: list[str],
) -> None:
    select_columns = ", ".join(columns)
    df = source.execute(f"SELECT {select_columns} FROM {table_name}").fetchdf()
    target.execute(f"DELETE FROM {table_name}")
    if df.empty:
        return

    target.register("temp_df", df)
    column_list = ", ".join(columns)
    target.execute(
        f"INSERT INTO {table_name} ({column_list}) SELECT {column_list} FROM temp_df"
    )
    target.unregister("temp_df")


def ensure_local_db_exists() -> None:
    local_db = str(app3.DB_PATH)
    with duckdb.connect(local_db) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS interactions (
                project_id INTEGER NOT NULL,
                interaction_timestamp TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                content VARCHAR NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                task_id VARCHAR,
                project_id INTEGER NOT NULL,
                source_timestamp TIMESTAMP NOT NULL,
                description VARCHAR NOT NULL,
                status VARCHAR NOT NULL DEFAULT 'open',
                comments VARCHAR,
                updated_at TIMESTAMP,
                completed_at TIMESTAMP
            )
            """
        )


def main() -> None:
    ensure_local_db_exists()
    reset_remote_schema()

    with duckdb.connect(str(app3.DB_PATH), read_only=True) as local_conn, app3.connect_db() as remote_conn:
        copy_table(
            local_conn,
            remote_conn,
            "interactions",
            ["project_id", "interaction_timestamp", "content"],
        )
        copy_table(
            local_conn,
            remote_conn,
            "tasks",
            [
                "task_id",
                "project_id",
                "source_timestamp",
                "description",
                "status",
                "comments",
                "updated_at",
                "completed_at",
            ],
        )

    print(f"Migrated local DuckDB data to {app3.get_database_target().split('?')[0]}")


if __name__ == "__main__":
    main()
