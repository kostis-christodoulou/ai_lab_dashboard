from __future__ import annotations

from collections import Counter
from datetime import datetime
from email.utils import parsedate_to_datetime
from functools import wraps
from html import escape
import os
from pathlib import Path
import re
import uuid
from urllib.parse import urlencode

import duckdb
from dotenv import load_dotenv
from flask import Flask, abort, redirect, request, session, url_for
import msal
import pandas as pd
from werkzeug.middleware.proxy_fix import ProxyFix

BASE_DIR = Path(__file__).resolve().parent
LOCAL_CSV_PATH = BASE_DIR / "projects.csv"
DEFAULT_MOTHERDUCK_DB = "ai_lab_dashboard"
DEFAULT_ALLOWED_EMAILS = {"kchristodoulou@london.edu"}

load_dotenv(BASE_DIR / ".env")

COLUMN_MAP = {
    "Id": "id",
    "mentors": "mentor",
    "Email": "submitter_email",
    "Name": "submitter_name",
    "Team name": "name",
    "Team Lead name & LBS email": "lead_raw",
    "Additional member details": "raw_members",
    "What LBS problem do you want to solve?": "full_problem",
    "What does success look like in 8 weeks?": "full_success",
    "What kind of support do you need from AI Lab?": "support",
}

SUMMARY_OVERRIDES = {
    16: "Stopping student clubs from wasting GBP380k on failed events via AI automation.",
    4: "Personalised AI career coaching and CRM for high-stakes MBA recruitment.",
    12: "An AI assistant that helps new students prioritize opportunities from day one.",
    9: "An assistant that unifies coursework, events, and career updates into one daily student briefing.",
    10: "AI menu management for on campus food outlets.",
    15: "AI marketplace connecting researchers and students with global funding sources.",
    1: "A frictionless map app to end the confusion of finding rooms on campus.",
    5: "Upgrading the EMS experience with calendar planning and concentration tracking.",
    3: "A unified inbox for all communication channels with autonomous AI hand-offs.",
}

EXCLUDED_PROJECT_IDS = {3}
NOTION_ENABLED_PROJECT_IDS = {12}
TASK_STATUS_LABELS = {
    "open": "To Do",
    "in_progress": "WIP",
    "done": "Done",
}

COLORS = {
    "primary": "#001e62",
    "background": "#f5f7fb",
    "surface": "#ffffff",
    "muted": "#6c757d",
    "border": "#d8deea",
}


def clean_text(value: object) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip()


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def connect_db(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    token = clean_text(os.getenv("MOTHERDUCK_TOKEN", ""))
    if not token:
        raise RuntimeError("MOTHERDUCK_TOKEN is required. This app is configured for MotherDuck only.")

    database_name = clean_text(os.getenv("MOTHERDUCK_DB", DEFAULT_MOTHERDUCK_DB)) or DEFAULT_MOTHERDUCK_DB
    conn = duckdb.connect(f"md:?motherduck_token={token}")
    if not read_only:
        conn.execute(f"CREATE DATABASE IF NOT EXISTS {quote_identifier(database_name)}")
    conn.execute(f"USE {quote_identifier(database_name)}")
    return conn


def parse_allowed_emails() -> set[str]:
    configured = clean_text(os.getenv("ALLOWED_LOGIN_EMAILS", ""))
    if not configured:
        return set(DEFAULT_ALLOWED_EMAILS)
    emails = {
        email.strip().lower()
        for email in re.split(r"[,\n;]+", configured)
        if email.strip()
    }
    return emails or set(DEFAULT_ALLOWED_EMAILS)


def azure_sso_enabled() -> bool:
    return clean_text(os.getenv("AZURE_SSO_ENABLED", "false")).lower() in {"1", "true", "yes", "on"}


def get_current_user() -> dict[str, str] | None:
    user = session.get("user")
    if not isinstance(user, dict):
        return None
    email = clean_text(user.get("email", "")).lower()
    if not email:
        return None
    user["email"] = email
    user["name"] = clean_text(user.get("name", "")) or email
    return user


def current_user_email() -> str:
    user = get_current_user()
    return user["email"] if user else "system"


def build_redirect_uri() -> str:
    configured = clean_text(os.getenv("AZURE_REDIRECT_URI", ""))
    if configured:
        return configured
    return url_for("auth_callback", _external=True)


def build_msal_app() -> msal.ConfidentialClientApplication:
    client_id = clean_text(os.getenv("AZURE_CLIENT_ID", ""))
    client_secret = clean_text(os.getenv("AZURE_CLIENT_SECRET", ""))
    tenant_id = clean_text(os.getenv("AZURE_TENANT_ID", ""))
    if not client_id or not client_secret or not tenant_id:
        raise RuntimeError(
            "AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, and AZURE_TENANT_ID are required for Azure SSO."
        )
    authority = f"https://login.microsoftonline.com/{tenant_id}"
    return msal.ConfidentialClientApplication(
        client_id=client_id,
        authority=authority,
        client_credential=client_secret,
    )


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if not azure_sso_enabled():
            return view(*args, **kwargs)
        if get_current_user() is None:
            next_url = request.full_path if request.query_string else request.path
            session["post_login_redirect"] = next_url.rstrip("?")
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped_view


def extract_user_identity(claims: dict[str, object]) -> tuple[str, str]:
    email = clean_text(
        claims.get("preferred_username")
        or claims.get("email")
        or claims.get("upn")
        or ""
    ).lower()
    name = clean_text(claims.get("name") or "") or email
    return email, name


def format_mentor_text(value: object) -> str:
    return clean_text(value).replace(" + ", " +\n")


def extract_name_and_email(value: str) -> tuple[str, str]:
    text = clean_text(value)
    email_match = re.search(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", text)
    email = email_match.group(0) if email_match else ""
    if not text:
        return "", email
    if email:
        name = text.replace(email, "")
        name = re.sub(r"[\s,:;]+$", "", name)
        name = re.sub(r"^[\s,:;]+", "", name)
        name = re.sub(r"\s{2,}", " ", name).strip()
        return name, email
    return text, ""


def summarize_problem(text: str, limit: int = 125) -> str:
    cleaned = re.sub(r"\s+", " ", clean_text(text))
    if not cleaned:
        return "No summary provided."
    first_sentence = re.split(r"(?<=[.!?])\s+", cleaned, maxsplit=1)[0].strip()
    candidate = first_sentence or cleaned
    for separator in [": ", " - ", " — ", ", but ", ", with ", ", due to ", ", resulting in "]:
        if separator in candidate:
            candidate = candidate.split(separator, 1)[0].strip()
            break
    if len(candidate) <= limit:
        return candidate if re.search(r"[.!?]$", candidate) else f"{candidate}."
    return f"{candidate[:limit].rsplit(' ', 1)[0].rstrip(' ,;:-')}..."


def summarize_interaction_content(text: str, limit: int = 110) -> str:
    cleaned = clean_text(text)
    if not cleaned:
        return "Empty interaction."
    subject_match = re.search(r"^Subject:\s*(.+)$", cleaned, flags=re.IGNORECASE | re.MULTILINE)
    if subject_match:
        summary = subject_match.group(1).strip()
    else:
        flattened = re.sub(r"\s+", " ", cleaned)
        summary = re.split(r"(?<=[.!?])\s+", flattened, maxsplit=1)[0].strip()
    if len(summary) <= limit:
        return summary if re.search(r"[.!?]$", summary) else f"{summary}."
    return f"{summary[:limit].rsplit(' ', 1)[0].rstrip(' ,;:-')}..."


def clean_interaction_content(text: str) -> str:
    cleaned = clean_text(text)
    if not cleaned:
        return ""
    artifact_patterns = [
        r"^Graphical user interface, text, application\s*$",
        r"^Description automatically generated\s*$",
    ]
    cleaned_lines: list[str] = []
    for raw_line in cleaned.splitlines():
        line = raw_line.replace("\u200b", "").strip()
        if not line:
            cleaned_lines.append("")
            continue
        if any(re.match(pattern, line, flags=re.IGNORECASE) for pattern in artifact_patterns):
            continue
        if re.fullmatch(r"[\W_]+", line):
            continue
        cleaned_lines.append(line)

    significant_lines = [line for line in cleaned_lines if line]
    duplicate_start = None
    for current_index in range(4, len(significant_lines) - 3):
        current_line = significant_lines[current_index]
        if len(current_line) < 12:
            continue
        for previous_index in range(0, current_index - 2):
            if significant_lines[previous_index] != current_line:
                continue
            window = min(6, len(significant_lines) - current_index, len(significant_lines) - previous_index)
            if window < 3:
                continue
            matches = sum(
                1
                for offset in range(window)
                if significant_lines[previous_index + offset] == significant_lines[current_index + offset]
            )
            if matches >= 3:
                duplicate_start = current_line
                break
        if duplicate_start:
            break
    if duplicate_start:
        seen_anchor = False
        deduped_lines: list[str] = []
        for line in cleaned_lines:
            if line == duplicate_start:
                if seen_anchor:
                    break
                seen_anchor = True
            deduped_lines.append(line)
        cleaned_lines = deduped_lines

    greeting_prefixes = ("hi ", "hello ", "dear ")
    non_empty_lines = [line for line in cleaned_lines if line]
    duplicate_restart = None
    for current_index in range(1, len(non_empty_lines) - 1):
        current_line = non_empty_lines[current_index]
        if not current_line.lower().startswith(greeting_prefixes):
            continue
        for previous_index in range(current_index):
            if non_empty_lines[previous_index] != current_line:
                continue
            next_window = min(3, len(non_empty_lines) - current_index, len(non_empty_lines) - previous_index)
            matches = sum(
                1
                for offset in range(next_window)
                if non_empty_lines[previous_index + offset] == non_empty_lines[current_index + offset]
            )
            if matches >= 2:
                duplicate_restart = current_line
                break
        if duplicate_restart:
            break
    if duplicate_restart:
        seen_restart = False
        trimmed_lines: list[str] = []
        for line in cleaned_lines:
            if line == duplicate_restart:
                if seen_restart:
                    break
                seen_restart = True
            trimmed_lines.append(line)
        cleaned_lines = trimmed_lines

    while cleaned_lines:
        last_line = cleaned_lines[-1]
        if not last_line:
            cleaned_lines.pop()
            continue
        if re.fullmatch(r"[A-Z][A-Za-z'.-]+(?:\s+[A-Z][A-Za-z'.-]+){1,3}", last_line):
            cleaned_lines.pop()
            continue
        break

    normalized_lines: list[str] = []
    blank_streak = 0
    for line in cleaned_lines:
        if line == "":
            blank_streak += 1
            if blank_streak > 1:
                continue
        else:
            blank_streak = 0
        normalized_lines.append(line)
    return "\n".join(normalized_lines).strip()


def extract_tasks_from_interaction(text: str) -> list[str]:
    cleaned = clean_interaction_content(text)
    if not cleaned:
        return []
    tasks: list[str] = []
    seen: set[str] = set()
    content_lines: list[str] = []
    heading_context: str | None = None

    def add_task(candidate: str) -> None:
        task = re.sub(r"\s+", " ", candidate).strip(" -:*")
        if len(task) < 8:
            return
        normalized = task.lower()
        if normalized in seen:
            return
        seen.add(normalized)
        tasks.append(task[0].upper() + task[1:] if task else task)

    def is_heading(line: str) -> bool:
        words = line.split()
        if not words or len(words) > 5:
            return False
        lowered = line.lower()
        return line.isupper() or " and " in lowered or " team" in lowered or lowered.endswith(" team")

    for line in cleaned.splitlines():
        stripped = line.strip()
        if not stripped:
            heading_context = None
            continue
        if re.match(r"^(from|to|cc|bcc|subject|date|sent):", stripped, flags=re.IGNORECASE):
            continue
        bullet_match = re.match(r"^[-*•]\s+(.+)$", stripped)
        numbered_match = re.match(r"^\d+[.)]\s+(.+)$", stripped)
        action_match = re.match(r"^(?:todo|to do|action item|next step|next steps)[:\-]?\s*(.+)$", stripped, flags=re.IGNORECASE)
        if bullet_match:
            add_task(bullet_match.group(1))
            continue
        if numbered_match:
            add_task(numbered_match.group(1))
            continue
        if action_match and action_match.group(1):
            add_task(action_match.group(1))
            continue
        if is_heading(stripped):
            heading_context = stripped
            continue
        if heading_context and len(stripped) < 120 and not re.search(r"[.!?]$", stripped):
            add_task(f"{heading_context}: {stripped}")
            continue
        content_lines.append(stripped)

    flattened = re.sub(r"\s+", " ", " ".join(content_lines))
    for sentence in re.split(r"(?<=[.!?])\s+", flattened):
        lowered = sentence.lower().strip()
        if not lowered:
            continue
        if any(lowered.startswith(prefix) for prefix in ["i hope ", "thank you ", "best,"]):
            continue
        if any(phrase in lowered for phrase in ["need to ", "needs to ", "please ", "follow up", "action item", "next step", "todo"]):
            add_task(sentence)
    return tasks


def extract_interaction_timestamp(text: str) -> datetime:
    cleaned = clean_text(text)
    for line in cleaned.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        if key.strip().lower() not in {"date", "sent"}:
            continue
        try:
            parsed = parsedate_to_datetime(value.strip())
            if parsed is not None:
                if parsed.tzinfo is not None:
                    return parsed.astimezone().replace(tzinfo=None)
                return parsed
        except (TypeError, ValueError, IndexError):
            continue
    return datetime.now()


def parse_team_details(lead_email: str, raw_members: str) -> tuple[list[str], str]:
    all_text = f"{clean_text(lead_email)} {clean_text(raw_members)}"
    emails = sorted(set(re.findall(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", all_text)))
    degrees = []
    for email in emails:
        match = re.search(r"\.([a-zA-Z]+)(\d{4})@", email)
        if match:
            degrees.append(match.group(1).upper())
    counts = Counter(degrees)
    composition = ", ".join(f"{count} {degree}" for degree, count in counts.items())
    return emails, composition or "1 LBS"


def load_projects_df() -> pd.DataFrame:
    raw_df = pd.read_csv(LOCAL_CSV_PATH).fillna("")
    df = raw_df.rename(columns=COLUMN_MAP)
    df["id"] = pd.to_numeric(df["id"], errors="coerce")
    df = df.dropna(subset=["id"]).copy()
    df["id"] = df["id"].astype(int)
    lead_details = df["lead_raw"].apply(extract_name_and_email)
    df["lead"] = lead_details.str[0].replace("", pd.NA).fillna(df["submitter_name"])
    df["lead_email"] = lead_details.str[1].replace("", pd.NA).fillna(df["submitter_email"])
    df["mentor"] = df["mentor"].apply(format_mentor_text)
    df["summary"] = df["id"].map(SUMMARY_OVERRIDES)
    df["summary"] = df["summary"].fillna(df["full_problem"].apply(summarize_problem))
    df = df[~df["id"].isin(EXCLUDED_PROJECT_IDS)].copy()
    team_details = df.apply(lambda row: parse_team_details(row["lead_email"], row["raw_members"]), axis=1)
    df["all_emails"] = team_details.str[0]
    df["team_composition"] = team_details.str[1]
    return df.sort_values(by="name").reset_index(drop=True)


def initialize_database(projects_df: pd.DataFrame) -> None:
    with connect_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY,
                name VARCHAR,
                lead VARCHAR,
                lead_email VARCHAR,
                raw_members VARCHAR,
                summary VARCHAR,
                full_problem VARCHAR,
                full_success VARCHAR,
                support VARCHAR,
                mentor VARCHAR,
                team_composition VARCHAR,
                all_emails_json VARCHAR
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS interactions (
                interaction_id VARCHAR,
                project_id INTEGER NOT NULL,
                interaction_timestamp TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                content VARCHAR NOT NULL
            )
            """
        )
        interaction_columns = {
            row[1] for row in conn.execute("PRAGMA table_info('interactions')").fetchall()
        }
        if "interaction_id" not in interaction_columns:
            conn.execute("ALTER TABLE interactions ADD COLUMN interaction_id VARCHAR")
            existing_rows = conn.execute(
                "SELECT rowid FROM interactions WHERE interaction_id IS NULL"
            ).fetchall()
            for (row_id,) in existing_rows:
                conn.execute(
                    "UPDATE interactions SET interaction_id = ? WHERE rowid = ?",
                    [str(uuid.uuid4()), row_id],
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
                created_at TIMESTAMP,
                created_by VARCHAR,
                updated_at TIMESTAMP,
                updated_by VARCHAR,
                completed_at TIMESTAMP,
                completed_by VARCHAR,
                deleted_at TIMESTAMP,
                deleted_by VARCHAR,
                source_system VARCHAR,
                source_label VARCHAR,
                notion_page_id VARCHAR,
                notion_data_source_id VARCHAR,
                notion_url VARCHAR,
                external_status VARCHAR,
                priority VARCHAR,
                start_date DATE,
                deadline DATE,
                assignee_names VARCHAR,
                assignee_emails VARCHAR,
                weekly_update VARCHAR,
                external_created_at TIMESTAMP,
                external_updated_at TIMESTAMP,
                sync_actor VARCHAR
            )
            """
        )
        task_columns = {
            row[1] for row in conn.execute("PRAGMA table_info('tasks')").fetchall()
        }
        for column_name, definition in [
            ("created_at", "TIMESTAMP"),
            ("created_by", "VARCHAR"),
            ("updated_by", "VARCHAR"),
            ("completed_by", "VARCHAR"),
            ("deleted_at", "TIMESTAMP"),
            ("deleted_by", "VARCHAR"),
            ("source_system", "VARCHAR"),
            ("source_label", "VARCHAR"),
            ("notion_page_id", "VARCHAR"),
            ("notion_data_source_id", "VARCHAR"),
            ("notion_url", "VARCHAR"),
            ("external_status", "VARCHAR"),
            ("priority", "VARCHAR"),
            ("start_date", "DATE"),
            ("deadline", "DATE"),
            ("assignee_names", "VARCHAR"),
            ("assignee_emails", "VARCHAR"),
            ("weekly_update", "VARCHAR"),
            ("external_created_at", "TIMESTAMP"),
            ("external_updated_at", "TIMESTAMP"),
            ("sync_actor", "VARCHAR"),
        ]:
            if column_name not in task_columns:
                conn.execute(f"ALTER TABLE tasks ADD COLUMN {column_name} {definition}")
        conn.execute(
            """
            UPDATE tasks
            SET created_at = COALESCE(created_at, source_timestamp),
                created_by = COALESCE(NULLIF(created_by, ''), 'system'),
                updated_at = COALESCE(updated_at, source_timestamp),
                updated_by = COALESCE(NULLIF(updated_by, ''), NULLIF(created_by, ''), 'system')
            """
        )
        conn.execute(
            """
            UPDATE tasks
            SET completed_by = CASE
                WHEN completed_at IS NOT NULL AND (completed_by IS NULL OR completed_by = '') THEN COALESCE(updated_by, created_by, 'system')
                ELSE completed_by
            END
            """
        )
        conn.execute("DELETE FROM projects")
        rows = [
            (
                int(row["id"]),
                row["name"],
                row["lead"],
                row["lead_email"],
                row["raw_members"],
                row["summary"],
                row["full_problem"],
                row["full_success"],
                row["support"],
                row["mentor"],
                row["team_composition"],
                "|".join(row["all_emails"]),
            )
            for _, row in projects_df.iterrows()
        ]
        if rows:
            conn.executemany(
                """
                INSERT INTO projects (
                    id, name, lead, lead_email, raw_members, summary,
                    full_problem, full_success, support, mentor,
                    team_composition, all_emails_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )


def fetch_projects() -> list[dict[str, object]]:
    projects_df = load_projects_df()
    initialize_database(projects_df)
    status_counts = fetch_task_status_counts()
    records = projects_df.to_dict("records")
    for record in records:
        project_id = int(record["id"])
        counts = status_counts.get(project_id, {})
        record["todo_task_count"] = counts.get("open", 0)
        record["wip_task_count"] = counts.get("in_progress", 0)
        record["done_task_count"] = counts.get("done", 0)
        record["has_notion"] = project_id in NOTION_ENABLED_PROJECT_IDS
    return records


def fetch_project(project_id: int) -> dict[str, object] | None:
    for record in fetch_projects():
        if int(record["id"]) == project_id:
            return record
    return None


def fetch_task_by_id(task_id: str) -> dict[str, str] | None:
    if not clean_text(task_id):
        return None
    with connect_db(read_only=True) as conn:
        row = conn.execute(
            """
            SELECT
                task_id,
                project_id,
                source_timestamp,
                description,
                status,
                comments,
                created_at,
                created_by,
                updated_at,
                updated_by,
                completed_at,
                completed_by
            FROM tasks
            WHERE task_id = ?
              AND deleted_at IS NULL
            LIMIT 1
            """,
            [task_id],
        ).fetchone()
    if not row:
        return None
    (
        found_task_id,
        project_id,
        timestamp,
        description,
        status,
        comments,
        created_at,
        created_by,
        updated_at,
        updated_by,
        completed_at,
        completed_by,
    ) = row
    return {
        "task_id": found_task_id,
        "project_id": str(project_id),
        "timestamp": timestamp.strftime("%d %b %Y, %H:%M") if isinstance(timestamp, datetime) else str(timestamp),
        "description": description,
        "status": status,
        "comments": comments or "",
        "created_at": created_at.strftime("%d %b %Y, %H:%M") if isinstance(created_at, datetime) else "",
        "created_by": created_by or "",
        "updated_at": updated_at.strftime("%d %b %Y, %H:%M") if isinstance(updated_at, datetime) else "",
        "updated_by": updated_by or "",
        "completed_timestamp": completed_at.strftime("%d %b %Y, %H:%M") if isinstance(completed_at, datetime) else "",
        "completed_by": completed_by or "",
    }


def fetch_project_interactions(project_id: int) -> list[dict[str, str]]:
    with connect_db(read_only=True) as conn:
        rows = conn.execute(
            """
            SELECT interaction_id, interaction_timestamp, content
            FROM interactions
            WHERE project_id = ?
            ORDER BY interaction_timestamp DESC
            """,
            [project_id],
        ).fetchall()
    return [
        {
            "interaction_id": interaction_id,
            "timestamp": timestamp.strftime("%d %b %Y, %H:%M") if isinstance(timestamp, datetime) else str(timestamp),
            "summary": summarize_interaction_content(content),
            "content": content,
        }
        for interaction_id, timestamp, content in rows
    ]


def fetch_project_tasks(project_id: int, status_filter: str | None = None) -> list[dict[str, str]]:
    query = """
        SELECT
            task_id,
            source_timestamp,
            description,
            status,
            comments,
            created_at,
            created_by,
            updated_at,
            updated_by,
            completed_at,
            completed_by
        FROM tasks
        WHERE project_id = ?
          AND deleted_at IS NULL
    """
    params: list[object] = [project_id]
    if status_filter:
        query += " AND status = ?"
        params.append(status_filter)
    query += " ORDER BY source_timestamp DESC, description ASC"
    with connect_db(read_only=True) as conn:
        rows = conn.execute(query, params).fetchall()
    return [
        {
            "task_id": task_id,
            "timestamp": timestamp.strftime("%d %b %Y, %H:%M") if isinstance(timestamp, datetime) else str(timestamp),
            "description": description,
            "status": status,
            "comments": comments or "",
            "created_at": created_at.strftime("%d %b %Y, %H:%M") if isinstance(created_at, datetime) else "",
            "created_by": created_by or "",
            "updated_at": updated_at.strftime("%d %b %Y, %H:%M") if isinstance(updated_at, datetime) else "",
            "updated_by": updated_by or "",
            "completed_timestamp": completed_at.strftime("%d %b %Y, %H:%M") if isinstance(completed_at, datetime) else "",
            "completed_by": completed_by or "",
        }
        for task_id, timestamp, description, status, comments, created_at, created_by, updated_at, updated_by, completed_at, completed_by in rows
    ]


def fetch_task_status_counts() -> dict[int, dict[str, int]]:
    with connect_db(read_only=True) as conn:
        rows = conn.execute(
            """
            SELECT project_id, status, COUNT(*)
            FROM tasks
            WHERE deleted_at IS NULL
              AND status IN ('open', 'in_progress', 'done')
            GROUP BY project_id, status
            """
        ).fetchall()
    counts: dict[int, dict[str, int]] = {}
    for project_id, status, count in rows:
        project_counts = counts.setdefault(int(project_id), {"open": 0, "in_progress": 0, "done": 0})
        project_counts[str(status)] = int(count)
    return counts


def add_interaction(project_id: int, content: str, actor_email: str) -> None:
    interaction_timestamp = extract_interaction_timestamp(content)
    cleaned_content = clean_interaction_content(content)
    extracted_tasks = extract_tasks_from_interaction(cleaned_content)
    with connect_db() as conn:
        conn.execute(
            """
            INSERT INTO interactions (interaction_id, project_id, interaction_timestamp, content)
            VALUES (?, ?, ?, ?)
            """,
            [str(uuid.uuid4()), project_id, interaction_timestamp, cleaned_content],
        )
        if extracted_tasks:
            conn.executemany(
                """
                INSERT INTO tasks (
                    task_id, project_id, source_timestamp, description,
                    status, comments, created_at, created_by, updated_at, updated_by, completed_at, completed_by
                )
                VALUES (?, ?, ?, ?, 'open', '', ?, ?, ?, ?, NULL, NULL)
                """,
                [
                    (
                        str(uuid.uuid4()),
                        project_id,
                        interaction_timestamp,
                        task,
                        interaction_timestamp,
                        actor_email,
                        interaction_timestamp,
                        actor_email,
                    )
                    for task in extracted_tasks
                ],
            )


def add_manual_task(project_id: int, description: str, comments: str = "", actor_email: str = "system") -> None:
    now = datetime.now()
    with connect_db() as conn:
        conn.execute(
            """
            INSERT INTO tasks (
                task_id, project_id, source_timestamp, description,
                status, comments, created_at, created_by, updated_at, updated_by, completed_at, completed_by
            )
            VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, NULL, NULL)
            """,
            [str(uuid.uuid4()), project_id, now, description, comments, now, actor_email, now, actor_email],
        )


def update_task(task_id: str, status: str, comments: str, actor_email: str) -> None:
    now = datetime.now()
    completed_at = now if status == "done" else None
    completed_by = actor_email if status == "done" else None
    with connect_db() as conn:
        conn.execute(
            """
            UPDATE tasks
            SET status = ?, comments = ?, updated_at = ?, updated_by = ?, completed_at = ?, completed_by = ?,
                deleted_at = CASE WHEN ? != 'deleted' THEN NULL ELSE deleted_at END,
                deleted_by = CASE WHEN ? != 'deleted' THEN NULL ELSE deleted_by END
            WHERE task_id = ?
            """,
            [status, comments, now, actor_email, completed_at, completed_by, status, status, task_id],
        )


def delete_task(task_id: str, actor_email: str) -> None:
    with connect_db() as conn:
        now = datetime.now()
        conn.execute(
            """
            UPDATE tasks
            SET status = 'deleted',
                updated_at = ?,
                updated_by = ?,
                deleted_at = ?,
                deleted_by = ?
            WHERE task_id = ?
            """,
            [now, actor_email, now, actor_email, task_id],
        )


def nl_to_br(text: str) -> str:
    return "<br>".join(escape(text).splitlines())


def base_html(title: str, body: str) -> str:
    user = get_current_user()
    user_html = ""
    if user:
        user_html = (
            f'<div class="user-meta"><span>{escape(user["name"])}</span>'
            f'<span class="muted" style="color:#dbe4ff;">{escape(user["email"])}</span>'
            f'<a class="header-link" href="{url_for("logout")}">Logout</a></div>'
        )
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>
  <style>
    body {{ margin:0; font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background:{COLORS['background']}; color:#1f2937; }}
    .header {{ background:{COLORS['primary']}; color:white; padding:14px 24px; }}
    .header-bar {{ max-width:1280px; margin:0 auto; display:flex; align-items:center; justify-content:space-between; gap:16px; }}
    .header h1 {{ margin:0; font-size:1.4rem; }}
    .user-meta {{ display:flex; align-items:center; gap:12px; font-size:.85rem; flex-wrap:wrap; justify-content:flex-end; }}
    .header-link {{ color:white; text-decoration:none; border:1px solid rgba(255,255,255,.35); padding:6px 10px; border-radius:999px; }}
    .container {{ max-width:1280px; margin:0 auto; padding:20px 24px 40px; }}
    .card {{ background:white; border:1px solid {COLORS['border']}; border-radius:12px; box-shadow:0 6px 18px rgba(15,23,42,.06); }}
    .table {{ width:100%; border-collapse:collapse; }}
    .table th, .table td {{ padding:12px 10px; border-bottom:1px solid #e5e7eb; vertical-align:top; }}
    .table th {{ text-align:left; font-size:.78rem; color:#6b7280; letter-spacing:.02em; }}
    .badge {{ display:inline-block; padding:3px 8px; border:1px solid {COLORS['border']}; border-radius:999px; font-size:.74rem; background:#f8fafc; }}
    .btn, button {{ display:inline-block; border:none; border-radius:8px; padding:8px 12px; font-size:.9rem; cursor:pointer; background:{COLORS['primary']}; color:white; text-decoration:none; }}
    .btn-secondary {{ background:white; color:{COLORS['primary']}; border:1px solid {COLORS['border']}; }}
    .btn-link {{ background:none; color:{COLORS['primary']}; padding:0; }}
    .danger {{ color:#b91c1c; }}
    .muted {{ color:{COLORS['muted']}; }}
    .tabs {{ display:flex; gap:10px; margin:18px 0 20px; }}
    .tab {{ padding:10px 14px; border-radius:999px; border:1px solid {COLORS['border']}; background:white; color:{COLORS['primary']}; text-decoration:none; font-weight:600; }}
    .tab.active {{ background:{COLORS['primary']}; color:white; border-color:{COLORS['primary']}; }}
    .grid-2 {{ display:grid; grid-template-columns:2fr 1fr; gap:20px; }}
    .stack > * + * {{ margin-top:16px; }}
    textarea, input, select {{ width:100%; box-sizing:border-box; border:1px solid #cbd5e1; border-radius:8px; padding:10px 12px; font:inherit; }}
    textarea {{ min-height:110px; }}
    .small {{ font-size:.84rem; }}
    .pre {{ white-space:pre-wrap; font-size:.88rem; line-height:1.5; }}
    .pillcount {{ min-width:28px; text-align:center; }}
    .row-actions form {{ display:inline; }}
    @media (max-width: 900px) {{
      .grid-2 {{ grid-template-columns:1fr; }}
      .table-responsive {{ overflow:auto; }}
    }}
  </style>
</head>
<body>
  <div class="header"><div class="header-bar"><h1>2026 AI Lab Dashboard</h1>{user_html}</div></div>
  <div class="container">{body}</div>
</body>
</html>"""


def project_tabs(project_id: int, active_tab: str) -> str:
    def tab(label: str, tab_id: str) -> str:
        cls = "tab active" if active_tab == tab_id else "tab"
        return f'<a class="{cls}" href="/project/{project_id}?tab={tab_id}">{escape(label)}</a>'
    return f'<div class="tabs">{tab("Tasks", "tasks")}{tab("Interactions", "interactions")}{tab("Project Details", "details")}</div>'


def render_home() -> str:
    projects = fetch_projects()
    rows = []
    for project in projects:
        mentor = nl_to_br(str(project["mentor"]))
        notion_icon = "&#10003;" if project["has_notion"] else "&times;"
        notion_color = "#15803d" if project["has_notion"] else "#b91c1c"
        project_id = int(project["id"])
        rows.append(
            f"""
            <tr>
              <td><strong style="color:{COLORS['primary']}">{escape(str(project['name']))}</strong><br><span class="small muted">{escape(str(project['summary']))}</span></td>
              <td><div class="small"><strong>{escape(str(project['lead']))}</strong><br><span class="badge">{escape(str(project['team_composition']))}</span></div></td>
              <td class="small" style="white-space:pre-wrap;color:{COLORS['primary']}">{mentor}</td>
              <td class="small" style="text-align:center;font-weight:700;color:{notion_color};">{notion_icon}</td>
              <td><a class="badge pillcount" href="/project/{project_id}?tab=tasks&status=open">{project['todo_task_count']}</a></td>
              <td><a class="badge pillcount" href="/project/{project_id}?tab=tasks&status=in_progress">{project['wip_task_count']}</a></td>
              <td><a class="badge pillcount" href="/project/{project_id}?tab=tasks&status=done">{project['done_task_count']}</a></td>
              <td><a class="btn btn-secondary" href="/project/{project_id}">View</a></td>
            </tr>
            """
        )
    body = f"""
      <div class="card table-responsive">
        <table class="table">
          <thead>
            <tr>
              <th>Project & Summary</th>
              <th>Lead / Team</th>
              <th>Mentors</th>
              <th>Notion</th>
              <th>To Do</th>
              <th>WIP</th>
              <th>Done</th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody>{''.join(rows)}</tbody>
        </table>
      </div>
    """
    return base_html("AI Lab Dashboard", body)


def render_tasks_tab(project: dict[str, object], selected_task_id: str | None, status_filter: str = "open", message: str = "") -> str:
    current_status = status_filter if status_filter in TASK_STATUS_LABELS else "open"
    visible_tasks = fetch_project_tasks(int(project["id"]), current_status)
    selected_task = fetch_task_by_id(selected_task_id) if selected_task_id else None
    if selected_task and int(selected_task["project_id"]) != int(project["id"]):
        selected_task = None

    filter_links = "".join(
        f'<a class="{"tab active" if current_status == status else "tab"}" href="/project/{project["id"]}?tab=tasks&status={status}">{label}</a>'
        for status, label in TASK_STATUS_LABELS.items()
    )
    task_rows = "".join(
        f"""
        <tr>
          <td>{escape(task['description'])}</td>
          <td class="small muted">{escape(task['timestamp'])}</td>
          <td class="small muted">{escape(task['status'].replace('_', ' ').title())}</td>
          <td class="small muted">{escape(task['completed_timestamp'] or '-')}</td>
          <td><a class="btn-link" href="/project/{project['id']}?tab=tasks&status={current_status}&task={task['task_id']}">Manage</a></td>
        </tr>
        """
        for task in visible_tasks
    ) or f'<tr><td colspan="5" class="muted">No {escape(TASK_STATUS_LABELS[current_status].lower())} tasks for this project yet.</td></tr>'
    editor = """
      <div class="muted">Click a task to manage its status and comments.</div>
    """
    if selected_task:
        editor = f"""
          <form method="post" action="/project/{project['id']}/tasks/update" class="stack">
            <input type="hidden" name="task_id" value="{escape(selected_task['task_id'])}">
            <input type="hidden" name="tab" value="tasks">
            <input type="hidden" name="status_filter" value="{escape(current_status)}">
            <div><strong style="color:{COLORS['primary']}">{escape(selected_task['description'])}</strong></div>
            <div class="small"><strong>Date Opened:</strong> {escape(selected_task['timestamp'])}</div>
            <div class="small"><strong>Created In App:</strong> {escape(selected_task['created_at'] or selected_task['timestamp'])}</div>
            <div class="small"><strong>Created By:</strong> {escape(selected_task['created_by'] or '-')}</div>
            <div class="small"><strong>Last Updated:</strong> {escape(selected_task['updated_at'] or '-')}</div>
            <div class="small"><strong>Last Updated By:</strong> {escape(selected_task['updated_by'] or '-')}</div>
            <div class="small"><strong>Date Completed:</strong> {escape(selected_task['completed_timestamp'] or '-')}</div>
            <div class="small"><strong>Completed By:</strong> {escape(selected_task['completed_by'] or '-')}</div>
            <label class="small muted">Status</label>
            <select name="status">
              {''.join(f'<option value="{value}"{" selected" if selected_task["status"] == value else ""}>{label}</option>' for value, label in [("open","Open"),("in_progress","In Progress"),("blocked","Blocked"),("done","Done")])}
            </select>
            <label class="small muted">Comments</label>
            <textarea name="comments">{escape(selected_task['comments'])}</textarea>
            <div style="display:flex;gap:10px;align-items:center;">
              <button type="submit">Save</button>
              <a class="btn btn-secondary" href="/project/{project['id']}?tab=tasks&status={current_status}">Close</a>
            </div>
          </form>
          <form method="post" action="/project/{project['id']}/tasks/delete" style="margin-top:12px;">
            <input type="hidden" name="task_id" value="{escape(selected_task['task_id'])}">
            <input type="hidden" name="tab" value="tasks">
            <input type="hidden" name="status_filter" value="{escape(current_status)}">
            <button type="submit" class="btn btn-secondary danger">Delete</button>
          </form>
        """
    return f"""
      <div class="stack">
        <div class="card" style="padding:16px;">
          <form method="post" action="/project/{project['id']}/tasks/manual" class="stack">
            <input type="hidden" name="tab" value="tasks">
            <input type="hidden" name="status_filter" value="{escape(current_status)}">
            <input name="description" placeholder="Add a manual task...">
            <textarea name="comments" placeholder="Optional comments..." style="min-height:72px;"></textarea>
            <div><button type="submit">Add Task</button></div>
          </form>
        </div>
        {f'<div class="small muted">{escape(message)}</div>' if message else ''}
        <div class="tabs">{filter_links}</div>
        <div class="card table-responsive">
          <table class="table">
            <thead><tr><th>Task</th><th>Date Opened</th><th>Status</th><th>Date Completed</th><th></th></tr></thead>
            <tbody>{task_rows}</tbody>
          </table>
        </div>
        <div class="card" style="padding:16px;">{editor}</div>
      </div>
    """


def render_interactions_tab(project: dict[str, object], selected_interaction_id: str | None, message: str = "") -> str:
    interactions = fetch_project_interactions(int(project["id"]))
    selected = next((item for item in interactions if item["interaction_id"] == selected_interaction_id), None)
    rows = "".join(
        f"""
        <tr>
          <td class="small muted">{escape(item['timestamp'])}</td>
          <td><a class="btn-link" href="/project/{project['id']}?tab=interactions&interaction={item['interaction_id']}">{escape(item['summary'])}</a></td>
        </tr>
        """
        for item in interactions
    ) or '<tr><td colspan="2" class="muted">No interactions recorded yet.</td></tr>'
    raw_view = '<div class="muted">Click an interaction to view the full raw content.</div>'
    if selected:
        raw_view = f"""
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
            <span class="small muted">{escape(selected['timestamp'])}</span>
            <a class="btn-link" href="/project/{project['id']}?tab=interactions">Close</a>
          </div>
          <div class="pre">{nl_to_br(selected['content'])}</div>
        """
    history = "".join(
        f'<div class="small" style="margin-bottom:12px;"><div class="muted">{escape(item["timestamp"])}</div><div>{escape(item["summary"])}</div></div>'
        for item in interactions
    ) or '<div class="muted">No interactions recorded yet.</div>'
    return f"""
      <div class="stack">
        {f'<div class="small muted">{escape(message)}</div>' if message else ''}
        <div class="card table-responsive">
          <table class="table">
            <thead><tr><th>Date</th><th>Summary</th></tr></thead>
            <tbody>{rows}</tbody>
          </table>
        </div>
        <div class="card" style="padding:16px;">{raw_view}</div>
        <div class="card" style="padding:16px;">
          <h3 style="margin-top:0;color:{COLORS['primary']};font-size:1rem;">Interaction History</h3>
          <form method="post" action="/project/{project['id']}/interactions/add" class="stack">
            <input type="hidden" name="tab" value="interactions">
            <textarea name="content" placeholder="Paste an email or meeting note here..."></textarea>
            <div><button type="submit">Add Interaction</button></div>
          </form>
          <div style="margin-top:16px;">{history}</div>
        </div>
      </div>
    """


def render_details_tab(project: dict[str, object]) -> str:
    emails = "".join(f"<li>{escape(email)}</li>" for email in project["all_emails"])
    mentor_html = nl_to_br(str(project["mentor"]))
    return f"""
      <div class="grid-2">
        <div class="card" style="padding:20px;">
          <h3 style="margin-top:0;color:{COLORS['primary']};font-size:1rem;">The Problem Submission</h3>
          <div class="pre">{nl_to_br(str(project['full_problem']))}</div>
          <hr style="margin:24px 0;border:none;border-top:1px solid #e5e7eb;">
          <h3 style="margin-top:0;color:{COLORS['primary']};font-size:1rem;">8-Week Success Metric</h3>
          <div class="pre">{nl_to_br(str(project['full_success']))}</div>
        </div>
        <div class="card" style="padding:20px;">
          <h3 style="margin-top:0;color:{COLORS['primary']};font-size:1rem;">Lab Contact Card</h3>
          <div class="small muted">Team Lead</div><div style="margin-bottom:10px;"><strong>{escape(str(project['lead']))}</strong></div>
          <div class="small muted">Lead Email</div><div style="margin-bottom:10px;">{escape(str(project['lead_email']))}</div>
          <div class="small muted">Mentor Team</div><div style="margin-bottom:10px;white-space:pre-wrap;color:{COLORS['primary']};"><strong>{mentor_html}</strong></div>
          <div class="small muted">Support Requested</div><div style="margin-bottom:10px;">{escape(str(project['support']))}</div>
          <div class="small muted">Team Composition</div><div style="margin-bottom:10px;">{escape(str(project['team_composition']))}</div>
          <div class="small muted">Participant Emails</div><ul>{emails}</ul>
        </div>
      </div>
    """


def render_project_page(project_id: int, tab: str = "tasks", selected_task_id: str | None = None, selected_interaction_id: str | None = None, task_status: str = "open", message: str = "") -> str:
    project = fetch_project(project_id)
    if not project:
        return base_html("Not Found", '<div class="card" style="padding:20px;">Project not found.</div>')
    if tab == "interactions":
        tab_content = render_interactions_tab(project, selected_interaction_id, message)
    elif tab == "details":
        tab_content = render_details_tab(project)
    else:
        tab = "tasks"
        tab_content = render_tasks_tab(project, selected_task_id, task_status, message)
    body = f"""
      <a class="btn btn-secondary" href="/">Back to dashboard</a>
      <h1 style="color:{COLORS['primary']};margin:18px 0 6px;">{escape(str(project['name']))}</h1>
      <p class="muted" style="margin-top:0;">{escape(str(project['summary']))}</p>
      {project_tabs(project_id, tab)}
      {tab_content}
    """
    return base_html(str(project["name"]), body)


app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)  # type: ignore[assignment]
app.config["SECRET_KEY"] = clean_text(os.getenv("FLASK_SECRET_KEY", "")) or str(uuid.uuid4())
app.config["SESSION_COOKIE_SECURE"] = clean_text(os.getenv("FLASK_ENV", "")).lower() != "development"
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
server = app


def render_login_page(error_message: str = "") -> str:
    body = f"""
      <div class="card" style="max-width:640px;margin:48px auto;padding:28px;">
        <h2 style="margin-top:0;color:{COLORS['primary']};">Sign in with Azure</h2>
        <p class="muted">Use your London Business School Microsoft account to access the dashboard.</p>
        <p class="small muted">Only approved email addresses can sign in.</p>
        {f'<div class="small danger" style="margin:12px 0;">{escape(error_message)}</div>' if error_message else ''}
        <a class="btn" href="{url_for("login")}">Continue with Microsoft</a>
      </div>
    """
    return base_html("Sign In", body)


@app.before_request
def enforce_login():
    if not azure_sso_enabled():
        return None
    allowed_paths = {"login", "auth_callback", "logout", "healthcheck"}
    if request.endpoint in allowed_paths or request.path.startswith("/static/"):
        return None
    if get_current_user() is None:
        next_url = request.full_path if request.query_string else request.path
        session["post_login_redirect"] = next_url.rstrip("?")
        return redirect(url_for("login"))
    return None


@app.get("/login")
def login():
    if not azure_sso_enabled():
        return redirect(url_for("home"))
    if get_current_user():
        return redirect(url_for("home"))
    try:
        msal_app = build_msal_app()
    except RuntimeError as exc:
        return render_login_page(str(exc))
    flow = msal_app.initiate_auth_code_flow(
        scopes=["openid", "profile", "email"],
        redirect_uri=build_redirect_uri(),
    )
    session["auth_flow"] = flow
    return redirect(flow["auth_uri"])


@app.get("/auth/callback")
def auth_callback():
    if not azure_sso_enabled():
        return redirect(url_for("home"))
    flow = session.get("auth_flow")
    if not flow:
        return render_login_page("Your session expired before Microsoft completed sign-in. Please try again.")
    try:
        result = build_msal_app().acquire_token_by_auth_code_flow(flow, request.args)
    except RuntimeError as exc:
        session.pop("auth_flow", None)
        return render_login_page(str(exc))
    except ValueError:
        session.pop("auth_flow", None)
        return render_login_page("Azure sign-in could not be completed. Please try again.")
    session.pop("auth_flow", None)
    if "error" in result:
        return render_login_page(result.get("error_description", "Azure sign-in failed."))
    claims = result.get("id_token_claims") or {}
    email, name = extract_user_identity(claims)
    allowed_emails = parse_allowed_emails()
    if not email or email not in allowed_emails:
        session.clear()
        return render_login_page("Your account is not on the allowed access list for this dashboard.")
    session["user"] = {"email": email, "name": name}
    redirect_target = clean_text(session.pop("post_login_redirect", "")) or url_for("home")
    return redirect(redirect_target)


@app.get("/logout")
def logout():
    if not azure_sso_enabled():
        return redirect(url_for("home"))
    session.clear()
    tenant_id = clean_text(os.getenv("AZURE_TENANT_ID", ""))
    post_logout_redirect = url_for("login", _external=True)
    if tenant_id:
        logout_url = (
            f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/logout?"
            f"{urlencode({'post_logout_redirect_uri': post_logout_redirect})}"
        )
        return redirect(logout_url)
    return redirect(post_logout_redirect)


@app.get("/healthz")
def healthcheck():
    return "ok"


@app.get("/")
@login_required
def home() -> str:
    return render_home()


@app.get("/project/<int:project_id>")
@login_required
def project_detail(project_id: int) -> str:
    return render_project_page(
        project_id,
        request.args.get("tab", "tasks"),
        request.args.get("task"),
        request.args.get("interaction"),
        request.args.get("status", "open"),
        request.args.get("message", ""),
    )


@app.post("/project/<int:project_id>/interactions/add")
@login_required
def interaction_add(project_id: int):
    content = clean_text(request.form.get("content", ""))
    if content:
        add_interaction(project_id, content, current_user_email())
        message = "Interaction saved."
    else:
        message = "Paste content into the box before saving."
    return redirect(f"/project/{project_id}?{urlencode({'tab': 'interactions', 'message': message})}")


@app.post("/project/<int:project_id>/tasks/manual")
@login_required
def task_manual(project_id: int):
    description = clean_text(request.form.get("description", ""))
    comments = clean_text(request.form.get("comments", ""))
    status_filter = clean_text(request.form.get("status_filter", "open")) or "open"
    if description:
        add_manual_task(project_id, description, comments, current_user_email())
        message = "Task added."
    else:
        message = "Enter a task description first."
    return redirect(f"/project/{project_id}?{urlencode({'tab': 'tasks', 'status': status_filter, 'message': message})}")


@app.post("/project/<int:project_id>/tasks/update")
@login_required
def task_update(project_id: int):
    task_id = clean_text(request.form.get("task_id", ""))
    status_filter = clean_text(request.form.get("status_filter", "open")) or "open"
    if task_id:
        new_status = clean_text(request.form.get("status", "open")) or "open"
        update_task(
            task_id,
            new_status,
            clean_text(request.form.get("comments", "")),
            current_user_email(),
        )
        status_filter = new_status if new_status in TASK_STATUS_LABELS else status_filter
    return redirect(f"/project/{project_id}?{urlencode({'tab': 'tasks', 'status': status_filter})}")


@app.post("/project/<int:project_id>/tasks/delete")
@login_required
def task_delete(project_id: int):
    task_id = clean_text(request.form.get("task_id", ""))
    status_filter = clean_text(request.form.get("status_filter", "open")) or "open"
    if task_id:
        delete_task(task_id, current_user_email())
    return redirect(f"/project/{project_id}?{urlencode({'tab': 'tasks', 'status': status_filter})}")


if __name__ == "__main__":
    app.run(debug=True)
