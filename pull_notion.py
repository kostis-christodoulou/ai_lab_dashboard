from __future__ import annotations

import argparse
from datetime import datetime, UTC
import json
import os
from pathlib import Path
import re
from typing import Any
import uuid

import app as dashboard_app
from dotenv import load_dotenv
import requests

NOTION_VERSION = "2026-03-11"
BASE_URL = "https://api.notion.com/v1"
DEFAULT_NOTION_URL = (os.getenv("NOTION_COMPASS_URL") or "").strip() or "https://www.notion.so/3410e160932f80fb8c2cc0c917c4e21a?v=3410e160932f80bf8378000cb9e418d1&source=copy_link"
DEFAULT_PROJECT_ID = int((os.getenv("NOTION_COMPASS_PROJECT_ID") or "12").strip())
SOURCE_CONFIG_PATH = Path(__file__).resolve().parent / "notion_sources.json"

load_dotenv(Path(__file__).resolve().parent / ".env")


def clean_text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def normalize_notion_id(value: str) -> str:
    compact = re.sub(r"[^0-9a-fA-F]", "", value)
    if len(compact) != 32:
        raise ValueError(f"Expected a 32-character Notion ID, got: {value}")
    return (
        f"{compact[:8]}-{compact[8:12]}-{compact[12:16]}-"
        f"{compact[16:20]}-{compact[20:]}"
    ).lower()


def extract_candidate_ids(notion_url: str) -> list[str]:
    matches = re.findall(r"[0-9a-fA-F]{32}", notion_url)
    candidates: list[str] = []
    seen: set[str] = set()
    for match in matches:
        normalized = normalize_notion_id(match)
        if normalized not in seen:
            seen.add(normalized)
            candidates.append(normalized)
    if not candidates:
        raise ValueError("No Notion IDs found in the supplied URL.")
    return candidates


class NotionProbe:
    def __init__(self, token: str, timeout: int = 30):
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Notion-Version": NOTION_VERSION,
                "Content-Type": "application/json",
            }
        )
        self.timeout = timeout

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> requests.Response:
        response = self.session.request(
            method=method,
            url=f"{BASE_URL}{path}",
            json=payload,
            timeout=self.timeout,
        )
        return response

    def get_json(self, method: str, path: str, payload: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
        response = self.request(method, path, payload)
        try:
            body = response.json()
        except ValueError:
            body = {"raw_text": response.text}
        return response.status_code, body

    def retrieve_database(self, database_id: str) -> tuple[int, dict[str, Any]]:
        return self.get_json("GET", f"/databases/{database_id}")

    def retrieve_data_source(self, data_source_id: str) -> tuple[int, dict[str, Any]]:
        return self.get_json("GET", f"/data_sources/{data_source_id}")

    def query_data_source(self, data_source_id: str, page_size: int) -> tuple[int, dict[str, Any]]:
        return self.get_json("POST", f"/data_sources/{data_source_id}/query", {"page_size": page_size})

    def retrieve_page(self, page_id: str) -> tuple[int, dict[str, Any]]:
        return self.get_json("GET", f"/pages/{page_id}")


def extract_title_from_page(page: dict[str, Any]) -> str:
    for property_value in page.get("properties", {}).values():
        if property_value.get("type") == "title":
            parts = property_value.get("title", [])
            return "".join(part.get("plain_text", "") for part in parts).strip()
    return ""


def summarize_page(page: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": page.get("id"),
        "title": extract_title_from_page(page) or page.get("url", ""),
        "url": page.get("url"),
        "created_time": page.get("created_time"),
        "last_edited_time": page.get("last_edited_time"),
        "parent": page.get("parent"),
        "property_names": sorted(page.get("properties", {}).keys()),
    }


def extract_property_text(property_value: dict[str, Any]) -> str:
    property_type = property_value.get("type")
    if property_type == "title":
        return "".join(part.get("plain_text", "") for part in property_value.get("title", [])).strip()
    if property_type == "rich_text":
        return "".join(part.get("plain_text", "") for part in property_value.get("rich_text", [])).strip()
    if property_type == "status":
        return clean_text((property_value.get("status") or {}).get("name"))
    if property_type == "select":
        return clean_text((property_value.get("select") or {}).get("name"))
    if property_type == "date":
        date_value = property_value.get("date") or {}
        return clean_text(date_value.get("start"))
    if property_type == "people":
        people = property_value.get("people", [])
        return ", ".join(
            clean_text((person.get("person") or {}).get("email")) or clean_text(person.get("name"))
            for person in people
            if clean_text((person.get("person") or {}).get("email")) or clean_text(person.get("name"))
        )
    return ""


def normalize_task_page(page: dict[str, Any], source_name: str) -> dict[str, Any]:
    properties = page.get("properties", {})
    weekly_update = extract_property_text(properties.get("Weekly Update", {}))
    assignees = properties.get("Assignee", {}).get("people", [])
    return {
        "source": source_name,
        "notion_page_id": page.get("id"),
        "title": extract_property_text(properties.get("Name", {})) or extract_title_from_page(page),
        "status": extract_property_text(properties.get("Status", {})) or "Unknown",
        "priority": extract_property_text(properties.get("Priority", {})) or "",
        "start_date": extract_property_text(properties.get("Start Date", {})) or None,
        "deadline": extract_property_text(properties.get("Deadline", {})) or None,
        "assignees": [
            {
                "name": clean_text(person.get("name")),
                "email": clean_text((person.get("person") or {}).get("email")),
            }
            for person in assignees
        ],
        "weekly_update": weekly_update or None,
        "created_time": page.get("created_time"),
        "last_edited_time": page.get("last_edited_time"),
        "url": page.get("url"),
    }


def notion_status_to_duckdb_status(value: str) -> str:
    normalized = clean_text(value).lower()
    mapping = {
        "backlog": "open",
        "to do": "open",
        "in progress": "in_progress",
        "done": "done",
    }
    return mapping.get(normalized, "open")


def build_duckdb_sync_preview(
    normalized_tasks: list[dict[str, Any]],
    *,
    project_id: int,
    data_source_id: str,
) -> dict[str, Any]:
    proposed_columns = [
        {"name": "source_system", "type": "VARCHAR", "example": "notion"},
        {"name": "source_label", "type": "VARCHAR", "example": "AI Lab Project Management"},
        {"name": "notion_page_id", "type": "VARCHAR", "example": "3410e160-932f-8098-ac8f-f16e29bbcec0"},
        {"name": "notion_data_source_id", "type": "VARCHAR", "example": data_source_id},
        {"name": "notion_url", "type": "VARCHAR", "example": "https://app.notion.com/p/..."},
        {"name": "external_status", "type": "VARCHAR", "example": "To Do"},
        {"name": "priority", "type": "VARCHAR", "example": "P1 - High"},
        {"name": "start_date", "type": "DATE", "example": "2026-04-14"},
        {"name": "deadline", "type": "DATE", "example": "2026-04-18"},
        {"name": "assignee_names", "type": "VARCHAR", "example": "Ana Vitória|Burcu Magemizoğlu"},
        {"name": "assignee_emails", "type": "VARCHAR", "example": "anavitoria11@gmail.com|burcu.magemizoglu@gmail.com"},
        {"name": "weekly_update", "type": "VARCHAR", "example": ""},
        {"name": "external_created_at", "type": "TIMESTAMP", "example": "2026-04-13T16:20:00.000Z"},
        {"name": "external_updated_at", "type": "TIMESTAMP", "example": "2026-04-13T16:26:00.000Z"},
        {"name": "sync_actor", "type": "VARCHAR", "example": "notion_sync"},
    ]
    duckdb_rows = []
    for task in normalized_tasks:
        assignee_names = [clean_text(person.get("name")) for person in task.get("assignees", []) if clean_text(person.get("name"))]
        assignee_emails = [clean_text(person.get("email")) for person in task.get("assignees", []) if clean_text(person.get("email"))]
        now_actor = "notion_sync"
        created_at = clean_text(task.get("created_time"))
        updated_at = clean_text(task.get("last_edited_time")) or created_at
        duckdb_rows.append(
            {
                "task_id": str(uuid.uuid4()),
                "project_id": project_id,
                "source_timestamp": created_at,
                "description": clean_text(task.get("title")),
                "status": notion_status_to_duckdb_status(clean_text(task.get("status"))),
                "comments": "",
                "created_at": created_at,
                "created_by": now_actor,
                "updated_at": updated_at,
                "updated_by": now_actor,
                "completed_at": updated_at if notion_status_to_duckdb_status(clean_text(task.get("status"))) == "done" else None,
                "completed_by": now_actor if notion_status_to_duckdb_status(clean_text(task.get("status"))) == "done" else None,
                "deleted_at": None,
                "deleted_by": None,
                "source_system": "notion",
                "source_label": clean_text(task.get("source")),
                "notion_page_id": clean_text(task.get("notion_page_id")),
                "notion_data_source_id": data_source_id,
                "notion_url": clean_text(task.get("url")),
                "external_status": clean_text(task.get("status")),
                "priority": clean_text(task.get("priority")),
                "start_date": clean_text(task.get("start_date")) or None,
                "deadline": clean_text(task.get("deadline")) or None,
                "assignee_names": "|".join(assignee_names),
                "assignee_emails": "|".join(assignee_emails),
                "weekly_update": clean_text(task.get("weekly_update")),
                "external_created_at": created_at,
                "external_updated_at": updated_at,
                "sync_actor": now_actor,
            }
        )
    return {
        "project_id": project_id,
        "data_source_id": data_source_id,
        "proposed_new_columns": proposed_columns,
        "row_count": len(duckdb_rows),
        "insert_preview": duckdb_rows,
    }


def load_notion_sources() -> list[dict[str, Any]]:
    if SOURCE_CONFIG_PATH.exists():
        raw_sources = json.loads(SOURCE_CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(raw_sources, list):
            raise ValueError("notion_sources.json must contain a JSON list.")
        return raw_sources
    return [
        {
            "key": "compass",
            "label": "LBS Compass",
            "project_id": DEFAULT_PROJECT_ID,
            "url": DEFAULT_NOTION_URL,
            "token_env_var": "NOTION_COMPASS_API_KEY",
            "enabled": True,
        }
    ]


def get_source_token(source: dict[str, Any]) -> str:
    token_env_var = clean_text(source.get("token_env_var")) or "NOTION_API_KEY"
    token = clean_text(os.getenv(token_env_var, "")) or clean_text(os.getenv("NOTION_API_KEY", ""))
    if not token:
        raise ValueError(f"Missing Notion token for source '{clean_text(source.get('key'))}'. Expected env var {token_env_var}.")
    return token


def ensure_dashboard_schema() -> None:
    dashboard_app.initialize_database(dashboard_app.load_projects_df())


def sync_preview_to_duckdb(preview: dict[str, Any]) -> dict[str, Any]:
    ensure_dashboard_schema()
    inserted = 0
    updated = 0
    synced_ids: list[str] = []
    with dashboard_app.connect_db() as conn:
        for row in preview.get("insert_preview", []):
            notion_page_id = clean_text(row.get("notion_page_id"))
            if not notion_page_id:
                continue
            existing = conn.execute(
                """
                SELECT task_id
                FROM tasks
                WHERE notion_page_id = ?
                LIMIT 1
                """,
                [notion_page_id],
            ).fetchone()
            synced_ids.append(notion_page_id)
            if existing:
                conn.execute(
                    """
                    UPDATE tasks
                    SET project_id = ?,
                        source_timestamp = ?,
                        description = ?,
                        status = ?,
                        updated_at = ?,
                        updated_by = ?,
                        completed_at = ?,
                        completed_by = ?,
                        deleted_at = NULL,
                        deleted_by = NULL,
                        source_system = ?,
                        source_label = ?,
                        notion_page_id = ?,
                        notion_data_source_id = ?,
                        notion_url = ?,
                        external_status = ?,
                        priority = ?,
                        start_date = ?,
                        deadline = ?,
                        assignee_names = ?,
                        assignee_emails = ?,
                        weekly_update = ?,
                        external_created_at = ?,
                        external_updated_at = ?,
                        sync_actor = ?
                    WHERE task_id = ?
                    """,
                    [
                        row["project_id"],
                        row["source_timestamp"],
                        row["description"],
                        row["status"],
                        row["updated_at"],
                        row["updated_by"],
                        row["completed_at"],
                        row["completed_by"],
                        row["source_system"],
                        row["source_label"],
                        row["notion_page_id"],
                        row["notion_data_source_id"],
                        row["notion_url"],
                        row["external_status"],
                        row["priority"],
                        row["start_date"],
                        row["deadline"],
                        row["assignee_names"],
                        row["assignee_emails"],
                        row["weekly_update"],
                        row["external_created_at"],
                        row["external_updated_at"],
                        row["sync_actor"],
                        existing[0],
                    ],
                )
                updated += 1
            else:
                conn.execute(
                    """
                    INSERT INTO tasks (
                        task_id, project_id, source_timestamp, description, status, comments,
                        created_at, created_by, updated_at, updated_by, completed_at, completed_by,
                        deleted_at, deleted_by, source_system, source_label, notion_page_id,
                        notion_data_source_id, notion_url, external_status, priority, start_date,
                        deadline, assignee_names, assignee_emails, weekly_update,
                        external_created_at, external_updated_at, sync_actor
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        row["task_id"],
                        row["project_id"],
                        row["source_timestamp"],
                        row["description"],
                        row["status"],
                        row["comments"],
                        row["created_at"],
                        row["created_by"],
                        row["updated_at"],
                        row["updated_by"],
                        row["completed_at"],
                        row["completed_by"],
                        row["deleted_at"],
                        row["deleted_by"],
                        row["source_system"],
                        row["source_label"],
                        row["notion_page_id"],
                        row["notion_data_source_id"],
                        row["notion_url"],
                        row["external_status"],
                        row["priority"],
                        row["start_date"],
                        row["deadline"],
                        row["assignee_names"],
                        row["assignee_emails"],
                        row["weekly_update"],
                        row["external_created_at"],
                        row["external_updated_at"],
                        row["sync_actor"],
                    ],
                )
                inserted += 1
        total_notion_rows = conn.execute(
            """
            SELECT COUNT(*)
            FROM tasks
            WHERE source_system = 'notion' AND project_id = ? AND deleted_at IS NULL
            """,
            [preview["project_id"]],
        ).fetchone()[0]
    return {
        "inserted": inserted,
        "updated": updated,
        "active_notion_tasks_for_project": total_notion_rows,
        "synced_notion_page_ids": synced_ids,
        "synced_at": datetime.now(UTC).isoformat(),
    }


def probe_group_page(notion_url: str, token: str, page_size: int = 10, project_id: int = DEFAULT_PROJECT_ID) -> dict[str, Any]:
    candidate_ids = extract_candidate_ids(notion_url)
    probe = NotionProbe(token=token)
    result: dict[str, Any] = {
        "notion_url": notion_url,
        "notion_version": NOTION_VERSION,
        "candidate_ids": candidate_ids,
        "attempts": [],
    }

    for notion_id in candidate_ids:
        database_status, database_body = probe.retrieve_database(notion_id)
        result["attempts"].append(
            {
                "id": notion_id,
                "endpoint": f"/databases/{notion_id}",
                "status": database_status,
                "object": database_body.get("object"),
                "code": database_body.get("code"),
                "message": database_body.get("message"),
            }
        )
        if database_status == 200 and database_body.get("object") == "database":
            data_sources = database_body.get("data_sources", [])
            database_result: dict[str, Any] = {
                "mode": "database",
                "database_id": database_body.get("id"),
                "database_title": database_body.get("title", []),
                "data_sources": data_sources,
            }
            if data_sources:
                first_data_source_id = clean_text(data_sources[0].get("id"))
                data_source_status, data_source_body = probe.retrieve_data_source(first_data_source_id)
                query_status, query_body = probe.query_data_source(first_data_source_id, page_size)
                normalized_tasks = [
                    normalize_task_page(
                        page,
                        clean_text(data_source_body.get("title", [{}])[0].get("plain_text")) or first_data_source_id,
                    )
                    for page in query_body.get("results", [])
                    if isinstance(page, dict) and page.get("object") == "page"
                ]
                database_result["first_data_source"] = {
                    "status": data_source_status,
                    "body": data_source_body,
                }
                database_result["query"] = {
                    "status": query_status,
                    "results_count": len(query_body.get("results", [])),
                    "has_more": query_body.get("has_more"),
                    "next_cursor": query_body.get("next_cursor"),
                    "results_preview": [
                        summarize_page(page)
                        for page in query_body.get("results", [])
                        if isinstance(page, dict) and page.get("object") == "page"
                    ],
                    "normalized_tasks": normalized_tasks,
                    "duckdb_sync_preview": build_duckdb_sync_preview(
                        normalized_tasks,
                        project_id=project_id,
                        data_source_id=first_data_source_id,
                    ),
                    "raw_body": query_body,
                }
            return database_result

        data_source_status, data_source_body = probe.retrieve_data_source(notion_id)
        result["attempts"].append(
            {
                "id": notion_id,
                "endpoint": f"/data_sources/{notion_id}",
                "status": data_source_status,
                "object": data_source_body.get("object"),
                "code": data_source_body.get("code"),
                "message": data_source_body.get("message"),
            }
        )
        if data_source_status == 200 and data_source_body.get("object") == "data_source":
            query_status, query_body = probe.query_data_source(notion_id, page_size)
            normalized_tasks = [
                normalize_task_page(
                    page,
                    clean_text(data_source_body.get("title", [{}])[0].get("plain_text")) or notion_id,
                )
                for page in query_body.get("results", [])
                if isinstance(page, dict) and page.get("object") == "page"
            ]
            return {
                "mode": "data_source",
                "data_source_id": notion_id,
                "data_source": data_source_body,
                "query": {
                    "status": query_status,
                    "results_count": len(query_body.get("results", [])),
                    "has_more": query_body.get("has_more"),
                    "next_cursor": query_body.get("next_cursor"),
                    "results_preview": [
                        summarize_page(page)
                        for page in query_body.get("results", [])
                        if isinstance(page, dict) and page.get("object") == "page"
                    ],
                    "normalized_tasks": normalized_tasks,
                    "duckdb_sync_preview": build_duckdb_sync_preview(
                        normalized_tasks,
                        project_id=project_id,
                        data_source_id=notion_id,
                    ),
                    "raw_body": query_body,
                },
            }

        page_status, page_body = probe.retrieve_page(notion_id)
        result["attempts"].append(
            {
                "id": notion_id,
                "endpoint": f"/pages/{notion_id}",
                "status": page_status,
                "object": page_body.get("object"),
                "code": page_body.get("code"),
                "message": page_body.get("message"),
            }
        )
        if page_status == 200 and page_body.get("object") == "page":
            return {
                "mode": "page",
                "page_id": notion_id,
                "page": summarize_page(page_body),
                "raw_body": page_body,
            }

    return {
        "mode": "unresolved",
        "details": result,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Pull Notion data for a shared group page or database.")
    parser.add_argument("--url", default="", help="Override the shared Notion URL to inspect.")
    parser.add_argument("--token", default="", help="Override the Notion integration token for a single run.")
    parser.add_argument("--page-size", type=int, default=10, help="Number of rows to fetch from a data source query.")
    parser.add_argument("--project-id", type=int, default=DEFAULT_PROJECT_ID, help="Project id to use in the DuckDB sync preview.")
    parser.add_argument("--source", default="", help="Specific source key from notion_sources.json, for example 'compass'.")
    parser.add_argument("--sync", action="store_true", help="Write the normalized Notion tasks into the shared DuckDB tasks table.")
    parser.add_argument("--out", default="", help="Optional JSON file path for the full response.")
    args = parser.parse_args()

    sources = load_notion_sources()
    if args.source:
        sources = [source for source in sources if clean_text(source.get("key")) == clean_text(args.source)]
        if not sources:
            raise SystemExit(f"No Notion source found for key '{args.source}'.")

    source_results: list[dict[str, Any]] = []
    for source in sources:
        if source.get("enabled", True) is False:
            continue
        notion_url = clean_text(args.url) or clean_text(source.get("url")) or DEFAULT_NOTION_URL
        token = clean_text(args.token) or get_source_token(source)
        project_id = int(source.get("project_id") or args.project_id)
        probe_result = probe_group_page(notion_url, token, args.page_size, project_id)
        wrapped_result: dict[str, Any] = {
            "source_key": clean_text(source.get("key")) or "unknown",
            "source_label": clean_text(source.get("label")) or clean_text(source.get("key")) or "Unknown source",
            "project_id": project_id,
            "probe": probe_result,
        }
        sync_preview = ((probe_result.get("query") or {}).get("duckdb_sync_preview") or {})
        if args.sync and sync_preview:
            wrapped_result["sync_summary"] = sync_preview_to_duckdb(sync_preview)
        source_results.append(wrapped_result)

    result: dict[str, Any]
    if len(source_results) == 1:
        result = source_results[0]
    else:
        result = {"sources": source_results}

    output = json.dumps(result, indent=2)
    print(output)

    if args.out:
        out_path = Path(args.out)
        out_path.write_text(output + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
