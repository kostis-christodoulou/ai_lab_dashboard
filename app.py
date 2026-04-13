from __future__ import annotations

from collections import Counter
from datetime import datetime
from email.utils import parsedate_to_datetime
import json
import os
from pathlib import Path
import re
import uuid

import dash
from dash import Input, Output, State, dcc, html
import dash_bootstrap_components as dbc
import duckdb
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
LOCAL_CSV_PATH = BASE_DIR / "projects.csv"
DEFAULT_MOTHERDUCK_DB = "ai_lab_dashboard"

# Notion scaffold
# Uncomment and configure these when you're ready to pull KPI data from Notion.
#
# import os
#
# NOTION_API_VERSION = "2022-06-28"
# NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID", "")
# NOTION_TOKEN = os.getenv("NOTION_TOKEN", "")
#
# def fetch_notion_progress_by_project() -> dict[str, dict[str, str]]:
#     """
#     Expected return shape:
#     {
#         "BrandOS": {
#             "Discovery": "Done",
#             "Build": "In Progress",
#             "Launch": "Blocked",
#         }
#     }
#     """
#     if not NOTION_DATABASE_ID or not NOTION_TOKEN:
#         return {}
#
#     response = requests.post(
#         f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query",
#         headers={
#             "Authorization": f"Bearer {NOTION_TOKEN}",
#             "Notion-Version": NOTION_API_VERSION,
#             "Content-Type": "application/json",
#         },
#         json={},
#         timeout=20,
#     )
#     response.raise_for_status()
#     payload = response.json()
#
#     project_progress = {}
#     for result in payload.get("results", []):
#         properties = result.get("properties", {})
#
#         # Adjust these property names to match your Notion database.
#         project_name = (
#             properties.get("Project", {})
#             .get("title", [{}])[0]
#             .get("plain_text", "")
#         )
#         discovery = (
#             properties.get("Discovery KPI", {})
#             .get("status", {})
#             .get("name", "KPI Pending")
#         )
#         build = (
#             properties.get("Build KPI", {})
#             .get("status", {})
#             .get("name", "KPI Pending")
#         )
#         launch = (
#             properties.get("Launch KPI", {})
#             .get("status", {})
#             .get("name", "KPI Pending")
#         )
#
#         if project_name:
#             project_progress[project_name] = {
#                 "Discovery": discovery,
#                 "Build": build,
#                 "Launch": launch,
#             }
#
#     return project_progress
#
# NOTION_PROGRESS = fetch_notion_progress_by_project()

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
    16: "Stopping student clubs from wasting £380k on failed events via AI automation.",
    4: "Personalised AI career coaching and CRM for high-stakes MBA recruitment.",
    12: "An AI assistant that helps new students prioritize opportunities from day one.",
    9: "A WhatsApp AI assistant that unifies coursework, events, and career updates into one daily student briefing.",
    10: "AI menu management for the cafeteria and a smarter EMS elective bidding system.",
    15: "AI marketplace connecting researchers and students with global funding sources.",
    1: "A frictionless map app to end the confusion of finding rooms on campus.",
    5: "Upgrading the EMS experience with calendar planning and concentration tracking.",
    3: "A unified inbox for all communication channels with autonomous AI hand-offs.",
}

EXCLUDED_PROJECT_IDS = {3}

COLORS = {
    "primary": "#001e62",
    "background": "#f5f7fb",
    "surface": "#ffffff",
    "muted": "#6c757d",
    "border": "#d8deea",
}

W = {
    "project": "40%",
    "lead_team": "16%",
    "mentors": "12%",
    "open_tasks": "8%",
    "progress": "18%",
    "action": "6%",
}


def clean_text(value: object) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip()


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def get_database_target() -> str:
    token = clean_text(os.getenv("MOTHERDUCK_TOKEN", ""))
    if not token:
        raise RuntimeError("MOTHERDUCK_TOKEN is required. This app is configured for MotherDuck only.")
    database_name = clean_text(
        os.getenv("MOTHERDUCK_DB", DEFAULT_MOTHERDUCK_DB)
    ) or DEFAULT_MOTHERDUCK_DB
    return f"md:{database_name}?motherduck_token={token}"


def connect_db(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    token = clean_text(os.getenv("MOTHERDUCK_TOKEN", ""))
    if not token:
        raise RuntimeError("MOTHERDUCK_TOKEN is required. Set it in your environment or Render service settings.")

    database_name = clean_text(
        os.getenv("MOTHERDUCK_DB", DEFAULT_MOTHERDUCK_DB)
    ) or DEFAULT_MOTHERDUCK_DB
    conn = duckdb.connect(f"md:?motherduck_token={token}")
    if not read_only:
        conn.execute(f"CREATE DATABASE IF NOT EXISTS {quote_identifier(database_name)}")
    conn.execute(f"USE {quote_identifier(database_name)}")
    return conn


def format_mentor_text(value: object) -> str:
    return clean_text(value).replace(" + ", " +\n")


def extract_name_and_email(value: str) -> tuple[str, str]:
    text = clean_text(value)
    email_match = re.search(
        r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", text
    )
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

    # Compress long opening sentences into a tighter one-line preview.
    for separator in [": ", " - ", " — ", ", but ", ", with ", ", due to ", ", resulting in "]:
        if separator in candidate:
            candidate = candidate.split(separator, 1)[0].strip()
            break

    replacements = [
        (r"^I want to\s+", ""),
        (r"^We want to\s+", ""),
        (r"^The idea is to build\s+", ""),
        (r"^The idea is to create\s+", ""),
        (r"^We are building\s+", ""),
        (r"^We're building\s+", ""),
        (r"^It'?s not necessarily LBS focused, we're building\s+", ""),
    ]
    for pattern, replacement in replacements:
        candidate = re.sub(pattern, replacement, candidate, flags=re.IGNORECASE).strip()

    if candidate:
        candidate = candidate[0].upper() + candidate[1:]

    if len(candidate) <= limit:
        return candidate if re.search(r"[.!?]$", candidate) else f"{candidate}."

    truncated = candidate[:limit].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return f"{truncated}..."


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

    trimmed = summary[:limit].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return f"{trimmed}..."


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

    # Detect when the clipboard payload contains the email body twice and truncate the duplicate tail.
    significant_lines = [line for line in cleaned_lines if line]
    duplicate_start = None
    min_anchor_length = 12
    for current_index in range(4, len(significant_lines) - 3):
        current_line = significant_lines[current_index]
        if len(current_line) < min_anchor_length:
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

    # Fallback for clipboard pastes that restart the body with the same greeting/opening.
    greeting_prefixes = ("hi ", "hello ", "dear ")
    non_empty_lines = [line for line in cleaned_lines if line]
    duplicate_restart = None
    for current_index in range(1, len(non_empty_lines) - 1):
        current_line = non_empty_lines[current_index]
        lowered = current_line.lower()
        if not lowered.startswith(greeting_prefixes):
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

    # Remove trailing standalone sender-name lines left behind by Outlook-style clipboard exports.
    while cleaned_lines:
        last_line = cleaned_lines[-1]
        if not last_line:
            cleaned_lines.pop()
            continue
        if re.fullmatch(r"[A-Z][A-Za-z'.-]+(?:\s+[A-Z][A-Za-z'.-]+){1,3}", last_line):
            cleaned_lines.pop()
            continue
        break

    # Collapse runaway blank lines after cleanup.
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
    skip_exact_lines = {
        "hi kostis!",
        "best,",
        "matt",
    }

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
        return (
            line.isupper()
            or " and " in lowered
            or " team" in lowered
            or lowered.endswith(" team")
        )

    for line in cleaned.splitlines():
        stripped = line.strip()
        if not stripped:
            heading_context = None
            continue

        if re.match(r"^(from|to|cc|bcc|subject|date|sent):", stripped, flags=re.IGNORECASE):
            continue

        if stripped.lower() in skip_exact_lines:
            continue

        bullet_match = re.match(r"^[-*•]\s+(.+)$", stripped)
        numbered_match = re.match(r"^\d+[.)]\s+(.+)$", stripped)
        action_match = re.match(
            r"^(?:todo|to do|action item|next step|next steps)[:\-]?\s*(.+)$",
            stripped,
            flags=re.IGNORECASE,
        )

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

        if (
            heading_context
            and len(stripped) < 120
            and not re.search(r"[.!?]$", stripped)
        ):
            add_task(f"{heading_context}: {stripped}")
            continue

        content_lines.append(stripped)

    flattened = re.sub(r"\s+", " ", " ".join(content_lines))
    for sentence in re.split(r"(?<=[.!?])\s+", flattened):
        sentence = sentence.strip()
        if not sentence:
            continue
        lowered = sentence.lower()
        if any(
            lowered.startswith(prefix)
            for prefix in [
                "i hope ",
                "as i mentioned during",
                "at this stage, i’m thinking",
                "at this stage, i'm thinking",
                "thank you ",
                "looking forward ",
                "if the last one ",
                "best,",
            ]
        ):
            continue
        if any(
            phrase in lowered
            for phrase in [
                "need help with",
                "need to talk to",
                "would need to",
                "talk to people from",
            ]
        ):
            add_task(sentence)
            continue
        if any(
            phrase in lowered
            for phrase in [
                "need to ",
                "needs to ",
                "please ",
                "follow up",
                "action item",
                "next step",
                "todo",
            ]
        ):
            add_task(sentence)

    return tasks


def extract_interaction_timestamp(text: str) -> datetime:
    cleaned = clean_text(text)
    if not cleaned:
        return datetime.now()

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
    email_matches = re.findall(
        r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", all_text
    )
    emails = sorted(set(email_matches))

    degrees = []
    for email in emails:
        match = re.search(r"\.([a-zA-Z]+)(\d{4})@", email)
        if match:
            degrees.append(match.group(1).upper())

    counts = Counter(degrees)
    composition = ", ".join(f"{count} {degree}" for degree, count in counts.items())
    return emails, composition or "1 LBS"


def load_local_dataframe() -> pd.DataFrame:
    raw_df = pd.read_csv(LOCAL_CSV_PATH).fillna("")
    df = raw_df.rename(columns=COLUMN_MAP)

    missing_columns = [value for value in COLUMN_MAP.values() if value not in df.columns]
    if missing_columns:
        raise ValueError(
            "Missing expected columns in Google Sheet: "
            + ", ".join(sorted(missing_columns))
        )

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

    team_details = df.apply(
        lambda row: parse_team_details(row["lead_email"], row["raw_members"]), axis=1
    )
    df["all_emails"] = team_details.str[0]
    df["team_composition"] = team_details.str[1]

    return df.sort_values(by="name").reset_index(drop=True)


def load_projects() -> tuple[pd.DataFrame, str | None]:
    try:
        return load_local_dataframe(), None
    except Exception as exc:  # noqa: BLE001
        return pd.DataFrame(), str(exc)


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
        task_columns = {
            row[1] for row in conn.execute("PRAGMA table_info('tasks')").fetchall()
        }
        if "task_id" not in task_columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN task_id VARCHAR")
            existing_rows = conn.execute(
                "SELECT rowid FROM tasks WHERE task_id IS NULL"
            ).fetchall()
            for (row_id,) in existing_rows:
                conn.execute(
                    "UPDATE tasks SET task_id = ? WHERE rowid = ?",
                    [str(uuid.uuid4()), row_id],
                )
        if "comments" not in task_columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN comments VARCHAR")
        if "updated_at" not in task_columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN updated_at TIMESTAMP")
        if "completed_at" not in task_columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN completed_at TIMESTAMP")
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
                    id,
                    name,
                    lead,
                    lead_email,
                    raw_members,
                    summary,
                    full_problem,
                    full_success,
                    support,
                    mentor,
                    team_composition,
                    all_emails_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )


def fetch_project_interactions(project_id: int) -> list[dict[str, str]]:
    with connect_db(read_only=True) as conn:
        rows = conn.execute(
            """
            SELECT interaction_timestamp, content
            FROM interactions
            WHERE project_id = ?
            ORDER BY interaction_timestamp DESC
            """,
            [project_id],
        ).fetchall()

    return [
        {
            "timestamp": (
                timestamp.strftime("%d %b %Y, %H:%M")
                if isinstance(timestamp, datetime)
                else str(timestamp)
            ),
            "summary": summarize_interaction_content(content),
            "content": content,
        }
        for timestamp, content in rows
    ]


def add_interaction(project_id: int, content: str) -> None:
    interaction_timestamp = extract_interaction_timestamp(content)
    cleaned_content = clean_interaction_content(content)
    extracted_tasks = extract_tasks_from_interaction(cleaned_content)
    with connect_db() as conn:
        conn.execute(
            """
            INSERT INTO interactions (project_id, interaction_timestamp, content)
            VALUES (?, ?, ?)
            """,
            [project_id, interaction_timestamp, cleaned_content],
        )
        if extracted_tasks:
            conn.executemany(
                """
                INSERT INTO tasks (
                    task_id,
                    project_id,
                    source_timestamp,
                    description,
                    status,
                    comments,
                    updated_at,
                    completed_at
                )
                VALUES (?, ?, ?, ?, 'open', '', ?, NULL)
                """,
                [
                    (
                        str(uuid.uuid4()),
                        project_id,
                        interaction_timestamp,
                        task,
                        interaction_timestamp,
                    )
                    for task in extracted_tasks
                ],
            )


def fetch_project_tasks(project_id: int) -> list[dict[str, str | None]]:
    with connect_db(read_only=True) as conn:
        rows = conn.execute(
            """
            SELECT task_id, source_timestamp, description, status, comments, completed_at
            FROM tasks
            WHERE project_id = ?
              AND status = 'open'
            ORDER BY source_timestamp DESC, description ASC
            """,
            [project_id],
        ).fetchall()

    return [
        {
            "task_id": task_id,
            "raw_timestamp": (
                timestamp.isoformat(sep=" ")
                if isinstance(timestamp, datetime)
                else str(timestamp)
            ),
            "timestamp": (
                timestamp.strftime("%d %b %Y, %H:%M")
                if isinstance(timestamp, datetime)
                else str(timestamp)
            ),
            "description": description,
            "status": status,
            "comments": comments or "",
            "completed_timestamp": (
                completed_at.strftime("%d %b %Y, %H:%M")
                if isinstance(completed_at, datetime)
                else ""
            ),
        }
        for task_id, timestamp, description, status, comments, completed_at in rows
    ]


def delete_task(task_id: str) -> None:
    with connect_db() as conn:
        conn.execute("DELETE FROM tasks WHERE task_id = ?", [task_id])


def update_task(task_id: str, status: str, comments: str) -> None:
    now = datetime.now()
    completed_at = now if status == "done" else None
    with connect_db() as conn:
        conn.execute(
            """
            UPDATE tasks
            SET status = ?,
                comments = ?,
                updated_at = ?,
                completed_at = ?
            WHERE task_id = ?
            """,
            [status, comments, now, completed_at, task_id],
        )


def add_manual_task(project_id: int, description: str, comments: str = "") -> None:
    now = datetime.now()
    with connect_db() as conn:
        conn.execute(
            """
            INSERT INTO tasks (
                task_id,
                project_id,
                source_timestamp,
                description,
                status,
                comments,
                updated_at,
                completed_at
            )
            VALUES (?, ?, ?, ?, 'open', ?, ?, NULL)
            """,
            [str(uuid.uuid4()), project_id, now, description, comments, now],
        )


def fetch_open_task_counts() -> dict[int, int]:
    with connect_db(read_only=True) as conn:
        rows = conn.execute(
            """
            SELECT project_id, COUNT(*)
            FROM tasks
            WHERE status = 'open'
            GROUP BY project_id
            """
        ).fetchall()

    return {int(project_id): int(count) for project_id, count in rows}


def render_interaction_history_items(items: list[dict[str, str]]) -> html.Div | html.P:
    if not items:
        return html.P("No interactions recorded yet.", className="text-muted mb-0")

    return html.Div(
        [
            html.Div(
                [
                    html.Small(
                        item["timestamp"],
                        className="text-muted d-block",
                    ),
                    html.P(
                        item["summary"],
                        className="mb-3",
                        style={
                            "fontSize": "0.88rem",
                            "whiteSpace": "pre-wrap",
                        },
                    ),
                ]
            )
            for item in items
        ]
    )


def render_raw_interaction_view(item: dict[str, str] | None) -> html.Div | html.P:
    if item is None:
        return html.Div(
            [
                html.Div(
                    [
                        html.Small("", className="text-muted d-block"),
                        dbc.Button(
                            "Close",
                            id="close-interaction-view",
                            color="link",
                            size="sm",
                            className="p-0 text-decoration-none",
                            style={
                                "fontSize": "0.8rem",
                                "visibility": "hidden",
                            },
                            disabled=True,
                        ),
                    ],
                    className="d-flex justify-content-between align-items-center mb-2",
                ),
                html.P(
                    "Click an interaction to view the full raw content.",
                    className="text-muted mb-0",
                ),
            ]
        )

    return html.Div(
        [
            html.Div(
                [
                    html.Small(item["timestamp"], className="text-muted d-block"),
                    dbc.Button(
                        "Close",
                        id="close-interaction-view",
                        color="link",
                        size="sm",
                        className="p-0 text-decoration-none",
                        style={"fontSize": "0.8rem"},
                    ),
                ],
                className="d-flex justify-content-between align-items-center mb-2",
            ),
            html.Pre(
                item["content"],
                className="mb-0",
                style={
                    "whiteSpace": "pre-wrap",
                    "fontSize": "0.82rem",
                    "fontFamily": "inherit",
                },
            ),
        ]
    )


def render_task_editor(task: dict[str, str | None] | None) -> html.Div:
    is_empty = task is None
    return html.Div(
        [
            html.Div(
                [
                    html.H6(
                        "Task Details",
                        className="mb-0",
                        style={"color": COLORS["primary"], "fontWeight": "700"},
                    ),
                    dbc.Button(
                        "Close",
                        id="close-task-editor",
                        color="link",
                        size="sm",
                        className="p-0 text-decoration-none",
                        style={
                            "fontSize": "0.8rem",
                            "visibility": "hidden" if is_empty else "visible",
                        },
                        disabled=is_empty,
                    ),
                ],
                className="d-flex justify-content-between align-items-center mb-3",
            ),
            html.Div(
                task["description"] if task else "Click a task to manage its status and comments.",
                id="task-editor-description",
                className="mb-3",
                style={
                    "fontSize": "0.9rem",
                    "color": COLORS["primary"] if task else COLORS["muted"],
                    "fontWeight": "600" if task else "400",
                },
            ),
            dbc.Row(
                [
                    dbc.Col(
                        [
                            html.Small("Date Opened", className="text-muted d-block"),
                            html.Div(
                                task["timestamp"] if task else "-",
                                id="task-editor-opened",
                                style={"fontSize": "0.84rem"},
                            ),
                        ],
                        width=4,
                    ),
                    dbc.Col(
                        [
                            html.Small("Date Completed", className="text-muted d-block"),
                            html.Div(
                                task["completed_timestamp"] if task and task["completed_timestamp"] else "-",
                                id="task-editor-completed",
                                style={"fontSize": "0.84rem"},
                            ),
                        ],
                        width=4,
                    ),
                    dbc.Col(
                        [
                            html.Small("Status", className="text-muted d-block"),
                            dbc.Select(
                                id="task-editor-status",
                                options=[
                                    {"label": "Open", "value": "open"},
                                    {"label": "In Progress", "value": "in_progress"},
                                    {"label": "Blocked", "value": "blocked"},
                                    {"label": "Done", "value": "done"},
                                ],
                                value=task["status"] if task else "open",
                                disabled=is_empty,
                                size="sm",
                            ),
                        ],
                        width=4,
                    ),
                ],
                className="g-3 mb-3",
            ),
            html.Small("Comments", className="text-muted d-block"),
            dbc.Textarea(
                id="task-editor-comments",
                value=task["comments"] if task else "",
                placeholder="Add notes, blockers, or follow-up comments...",
                disabled=is_empty,
                style={"minHeight": "100px", "fontSize": "0.85rem"},
                className="mb-3",
            ),
            html.Div(
                [
                    dbc.Button(
                        "Save",
                        id="save-task-btn",
                        color="primary",
                        size="sm",
                        disabled=is_empty,
                    ),
                    dbc.Button(
                        "Delete",
                        id="delete-task-btn",
                        color="link",
                        size="sm",
                        className="text-danger text-decoration-none",
                        disabled=is_empty,
                    ),
                ],
                className="d-flex gap-2 align-items-center",
            ),
        ]
    )


def render_tasks_table(items: list[dict[str, str]]) -> html.Div | html.P:
    table_or_empty = (
        html.P(
            "No open tasks detected from interactions yet.",
            className="text-muted mb-3",
        )
        if not items
        else dbc.Table(
            [
                html.Thead(
                    html.Tr(
                        [
                            html.Th("Task"),
                            html.Th("Date Opened", style={"width": "18%"}),
                            html.Th("Status", style={"width": "12%"}),
                            html.Th("Date Completed", style={"width": "18%"}),
                            html.Th("", style={"width": "10%"}),
                        ]
                    )
                ),
                html.Tbody(
                    [
                        html.Tr(
                            [
                                html.Td(
                                    item["description"],
                                    style={"fontSize": "0.85rem"},
                                ),
                                html.Td(
                                    item["timestamp"],
                                    style={
                                        "fontSize": "0.8rem",
                                        "color": COLORS["muted"],
                                        "whiteSpace": "nowrap",
                                    },
                                ),
                                html.Td(
                                    item["status"].replace("_", " ").title(),
                                    style={
                                        "fontSize": "0.8rem",
                                        "color": COLORS["muted"],
                                        "whiteSpace": "nowrap",
                                    },
                                ),
                                html.Td(
                                    item["completed_timestamp"] or "-",
                                    style={
                                        "fontSize": "0.8rem",
                                        "color": COLORS["muted"],
                                        "whiteSpace": "nowrap",
                                    },
                                ),
                                html.Td(
                                    dbc.Button(
                                        "Manage",
                                        id={"type": "task-row", "index": idx},
                                        color="link",
                                        size="sm",
                                        className="p-0 text-decoration-none",
                                        style={"fontSize": "0.82rem"},
                                    ),
                                    className="text-end",
                                ),
                            ]
                        )
                        for idx, item in enumerate(items)
                    ]
                ),
            ],
            bordered=False,
            hover=True,
            responsive=True,
            size="sm",
            className="mb-2 bg-white shadow-sm rounded",
            style={"overflow": "hidden"},
        )
    )

    return html.Div(
        [
            table_or_empty,
            html.Div(
                id="task-editor-view",
                children=render_task_editor(None),
                className="bg-white border rounded shadow-sm p-3",
            ),
        ]
    )


def render_interactions_table(items: list[dict[str, str]]) -> html.Div | html.P:
    table_or_empty = (
        html.P("No interactions recorded yet.", className="text-muted mb-3")
        if not items
        else dbc.Table(
            [
                html.Thead(
                    html.Tr(
                        [
                            html.Th("Date", style={"width": "26%"}),
                            html.Th("Summary"),
                        ]
                    )
                ),
                html.Tbody(
                    [
                        html.Tr(
                            [
                                html.Td(
                                    item["timestamp"],
                                    style={
                                        "fontSize": "0.8rem",
                                        "color": COLORS["muted"],
                                        "whiteSpace": "nowrap",
                                    },
                                ),
                                html.Td(
                                    dbc.Button(
                                        item["summary"],
                                        id={"type": "interaction-row", "index": idx},
                                        color="link",
                                        className="p-0 text-start text-decoration-none",
                                        style={
                                            "fontSize": "0.86rem",
                                            "lineHeight": "1.35",
                                            "color": COLORS["primary"],
                                        },
                                    )
                                ),
                            ]
                        )
                        for idx, item in enumerate(items)
                    ]
                ),
            ],
            bordered=False,
            hover=True,
            responsive=True,
            size="sm",
            className="mb-3 bg-white shadow-sm rounded",
            style={"overflow": "hidden"},
        )
    )

    return html.Div(
        [
            table_or_empty,
            html.Div(
                id="interaction-raw-view",
                children=render_raw_interaction_view(None),
                className="bg-white border rounded shadow-sm p-3",
            ),
        ],
    )


PROJECTS_DF, DATA_ERROR = load_projects()
if DATA_ERROR is None and os.getenv("SKIP_DB_INIT") != "1":
    initialize_database(PROJECTS_DF)

app = dash.Dash(
    __name__,
    external_stylesheets=[dbc.themes.BOOTSTRAP],
    suppress_callback_exceptions=True,
)
server = app.server


def make_task_mini_bar(label: str, value: str, progress: int = 8) -> html.Div:
    return html.Div(
        [
            html.Div(
                [
                    html.Small(label, style={"font-size": "0.56rem", "color": COLORS["muted"]}),
                    html.Small(
                        value,
                        style={
                            "font-size": "0.54rem",
                            "font-weight": "700",
                            "color": COLORS["primary"],
                        },
                    ),
                ],
                className="d-flex justify-content-between",
            ),
            dbc.Progress(value=progress, color="info", style={"height": "3px"}),
        ],
        style={"margin-bottom": "3px"},
    )


def get_progress_items(row: pd.Series) -> list[tuple[str, str, int]]:
    # Swap these placeholders for NOTION_PROGRESS[row["name"]] once the API scaffold
    # above is enabled and your KPI fields are mapped.
    return [
        ("Discovery", "KPI Pending", 10),
        ("Build", "KPI Pending", 10),
        ("Launch", "KPI Pending", 10),
    ]


def make_project_row(row: pd.Series, open_task_counts: dict[int, int]) -> html.Div:
    open_task_count = open_task_counts.get(int(row["id"]), 0)
    return html.Div(
        [
            html.Div(
                [
                    html.H6(
                        row["name"],
                        style={
                            "font-weight": "700",
                            "margin-bottom": "0.15rem",
                            "color": COLORS["primary"],
                        },
                    ),
                    html.P(
                        row["summary"],
                        style={
                            "font-size": "0.74rem",
                            "margin-bottom": "0",
                            "color": COLORS["muted"],
                            "line-height": "1.3",
                        },
                    ),
                ],
                style={"width": W["project"], "padding-right": "12px"},
            ),
            html.Div(
                [
                    html.Small(
                        row["lead"],
                        style={
                            "font-weight": "700",
                            "display": "block",
                            "font-size": "0.74rem",
                            "line-height": "1.2",
                            "margin-bottom": "4px",
                        },
                    ),
                    dbc.Badge(
                        row["team_composition"],
                        color="light",
                        text_color="dark",
                        style={
                            "font-size": "0.58rem",
                            "border": f"1px solid {COLORS['border']}",
                        },
                    ),
                ],
                style={"width": W["lead_team"], "padding-right": "10px"},
            ),
            html.Div(
                html.Small(
                    row["mentor"],
                    style={
                        "font-size": "0.68rem",
                        "color": COLORS["primary"],
                        "white-space": "pre-wrap",
                        "line-height": "1.15",
                    },
                ),
                style={"width": W["mentors"], "padding-right": "10px"},
            ),
            html.Div(
                dbc.Badge(
                    str(open_task_count),
                    color="light",
                    text_color="dark",
                    style={
                        "font-size": "0.66rem",
                        "border": f"1px solid {COLORS['border']}",
                        "min-width": "32px",
                    },
                ),
                style={"width": W["open_tasks"], "padding-right": "10px"},
            ),
            html.Div(
                [make_task_mini_bar(label, value, progress) for label, value, progress in get_progress_items(row)],
                style={"width": W["progress"], "padding-right": "10px"},
            ),
            html.Div(
                dbc.Button(
                    "View",
                    href=f"/project/{row['id']}",
                    size="sm",
                    color="dark",
                    outline=True,
                    className="w-100",
                    style={"font-size": "0.65rem"},
                ),
                style={"width": W["action"]},
            ),
        ],
        style={
            "display": "flex",
            "align-items": "center",
            "backgroundColor": COLORS["surface"],
            "padding": "9px 16px",
            "borderRadius": "8px",
            "border": f"1px solid {COLORS['border']}",
            "margin-bottom": "8px",
        },
        className="shadow-sm",
    )


def render_not_found() -> dbc.Container:
    return dbc.Container(
        dbc.Alert(
            [
                html.H4("Project not found", className="alert-heading"),
                html.P("The link is invalid or the project no longer exists in the sheet."),
                dbc.Button("Back to dashboard", href="/", color="primary"),
            ],
            color="warning",
            className="mt-4",
        ),
        fluid=True,
    )


def render_data_error(error_message: str) -> dbc.Container:
    return dbc.Container(
        dbc.Alert(
            [
                html.H4("Unable to load the dashboard data", className="alert-heading"),
                html.P("The local CSV could not be loaded right now."),
                html.Code(error_message),
            ],
            color="danger",
            className="mt-4",
        ),
        fluid=True,
    )


def render_project_detail(project: pd.Series) -> dbc.Container:
    interactions = fetch_project_interactions(int(project["id"]))
    tasks = fetch_project_tasks(int(project["id"]))
    return dbc.Container(
        [
            dcc.Store(id="interaction-store", data=interactions),
            dcc.Store(id="task-store", data=tasks),
            dcc.Store(id="selected-task-store", data=None),
            dbc.Button(
                "← Cohort Overview",
                href="/",
                color="link",
                className="p-0 mb-4",
                style={"color": COLORS["primary"]},
            ),
            html.H1(
                project["name"],
                style={
                    "font-weight": "900",
                    "color": COLORS["primary"],
                    "font-size": "2rem",
                    "margin-bottom": "0.35rem",
                },
            ),
            html.P(
                project["summary"],
                className="text-muted mb-3",
                style={"font-size": "1rem", "line-height": "1.4"},
            ),
            dbc.Tabs(
                [
                    dbc.Tab(
                        label="Tasks",
                        tab_id="tasks-tab",
                        children=[
                            html.Div(
                                [
                                    html.H5(
                                        "Open Tasks",
                                        style={
                                            "font-weight": "700",
                                            "color": COLORS["primary"],
                                            "font-size": "1rem",
                                        },
                                    ),
                                    dbc.Card(
                                        [
                                            dbc.CardBody(
                                                [
                                                    dbc.Input(
                                                        id="manual-task-input",
                                                        placeholder="Add a manual task...",
                                                        type="text",
                                                        className="mb-2",
                                                    ),
                                                    dbc.Textarea(
                                                        id="manual-task-comments",
                                                        placeholder="Optional comments...",
                                                        style={
                                                            "minHeight": "72px",
                                                            "fontSize": "0.84rem",
                                                        },
                                                        className="mb-2",
                                                    ),
                                                    dbc.Button(
                                                        "Add Task",
                                                        id="add-manual-task-btn",
                                                        color="primary",
                                                        size="sm",
                                                    ),
                                                    html.Div(
                                                        id="manual-task-save-status",
                                                        className="small text-muted mt-2",
                                                    ),
                                                ]
                                            )
                                        ],
                                        className="border-0 shadow-sm mb-3",
                                    ),
                                    render_tasks_table(tasks),
                                ],
                                className="pt-4",
                            )
                        ],
                    ),
                    dbc.Tab(
                        label="Interactions",
                        tab_id="interactions-tab",
                        children=[
                            html.Div(
                                [
                                    html.H5(
                                        "Interactions",
                                        style={
                                            "font-weight": "700",
                                            "color": COLORS["primary"],
                                            "font-size": "1rem",
                                        },
                                    ),
                                    render_interactions_table(interactions),
                                    dbc.Card(
                                        [
                                            dbc.CardHeader(
                                                "Interaction History",
                                                style={
                                                    "background": "#eef3ff",
                                                    "color": COLORS["primary"],
                                                    "fontWeight": "700",
                                                },
                                            ),
                                            dbc.CardBody(
                                                [
                                                    dbc.Textarea(
                                                        id="interaction-input",
                                                        placeholder="Paste an email or meeting note here...",
                                                        style={
                                                            "minHeight": "110px",
                                                            "marginBottom": "10px",
                                                            "fontSize": "0.86rem",
                                                        },
                                                    ),
                                                    dbc.Button(
                                                        "Add Interaction",
                                                        id="add-interaction-btn",
                                                        color="primary",
                                                        size="sm",
                                                        className="mb-2",
                                                    ),
                                                    html.Div(
                                                        id="interaction-save-status",
                                                        className="small text-muted mb-3",
                                                    ),
                                                    html.Div(
                                                        render_interaction_history_items(interactions),
                                                        id="interaction-history-list",
                                                    )
                                                ],
                                            ),
                                        ],
                                        className="border-0 shadow-sm mt-4",
                                    ),
                                ],
                                className="pt-4",
                            )
                        ],
                    ),
                    dbc.Tab(
                        label="Project Details",
                        tab_id="details-tab",
                        children=[
                            html.Div(
                                [
                                    dbc.Row(
                                        [
                                            dbc.Col(
                                                [
                                                    html.Div(
                                                        [
                                                            html.H5(
                                                                "The Problem Submission",
                                                                style={
                                                                    "font-weight": "bold",
                                                                    "font-size": "1rem",
                                                                },
                                                            ),
                                                            html.P(
                                                                project["full_problem"],
                                                                style={
                                                                    "white-space": "pre-wrap",
                                                                    "line-height": "1.6",
                                                                    "font-size": "0.92rem",
                                                                },
                                                            ),
                                                            html.Hr(className="my-5"),
                                                            html.H5(
                                                                "8-Week Success Metric",
                                                                style={
                                                                    "font-weight": "bold",
                                                                    "font-size": "1rem",
                                                                },
                                                            ),
                                                            html.P(
                                                                project["full_success"],
                                                                style={
                                                                    "white-space": "pre-wrap",
                                                                    "line-height": "1.6",
                                                                    "font-size": "0.92rem",
                                                                },
                                                            ),
                                                        ],
                                                        className="bg-white p-4 border shadow-sm rounded-3",
                                                    ),
                                                ],
                                                width=8,
                                            ),
                                            dbc.Col(
                                                [
                                                    dbc.Card(
                                                        [
                                                            dbc.CardHeader(
                                                                "Lab Contact Card",
                                                                style={
                                                                    "background": COLORS["primary"],
                                                                    "color": "white",
                                                                },
                                                            ),
                                                            dbc.CardBody(
                                                                [
                                                                    html.Small(
                                                                        "Team Lead",
                                                                        className="text-muted d-block",
                                                                    ),
                                                                    html.P(
                                                                        project["lead"],
                                                                        style={
                                                                            "font-weight": "700",
                                                                            "font-size": "0.92rem",
                                                                        },
                                                                    ),
                                                                    html.Small(
                                                                        "Lead Email",
                                                                        className="text-muted d-block",
                                                                    ),
                                                                    html.P(
                                                                        project["lead_email"],
                                                                        style={"font-size": "0.88rem"},
                                                                    ),
                                                                    html.Small(
                                                                        "Mentor Team",
                                                                        className="text-muted d-block",
                                                                    ),
                                                                    html.P(
                                                                        project["mentor"],
                                                                        style={
                                                                            "font-weight": "700",
                                                                            "color": COLORS["primary"],
                                                                            "white-space": "pre-wrap",
                                                                            "font-size": "0.88rem",
                                                                        },
                                                                    ),
                                                                    html.Small(
                                                                        "Support Requested",
                                                                        className="text-muted d-block",
                                                                    ),
                                                                    html.P(
                                                                        project["support"],
                                                                        style={
                                                                            "font-weight": "600",
                                                                            "font-size": "0.88rem",
                                                                        },
                                                                    ),
                                                                    html.Hr(),
                                                                    html.Small(
                                                                        "Team Composition",
                                                                        className="text-muted d-block",
                                                                    ),
                                                                    html.P(
                                                                        project["team_composition"],
                                                                        style={
                                                                            "font-weight": "600",
                                                                            "font-size": "0.88rem",
                                                                        },
                                                                    ),
                                                                    html.Small(
                                                                        "Participant Emails",
                                                                        className="text-muted d-block",
                                                                    ),
                                                                    html.Ul(
                                                                        [
                                                                            html.Li(
                                                                                email,
                                                                                style={"font-size": "0.8rem"},
                                                                            )
                                                                            for email in project["all_emails"]
                                                                        ],
                                                                        className="mb-0 ps-3",
                                                                    ),
                                                                ]
                                                            ),
                                                        ],
                                                        className="border-0 shadow-sm",
                                                    )
                                                ],
                                                width=4,
                                            ),
                                        ],
                                        className="g-4",
                                    ),
                                ],
                                className="pt-4",
                            )
                        ],
                    ),
                ],
                active_tab="tasks-tab",
            ),
        ],
        fluid=True,
    )


def render_dashboard(df: pd.DataFrame) -> html.Div:
    open_task_counts = fetch_open_task_counts()
    return html.Div(
        [
            html.Div(
                [
                    html.Small(
                        "PROJECT & SUMMARY",
                        style={"width": W["project"], "font-weight": "bold", "color": "#7a7a7a"},
                    ),
                    html.Small(
                        "LEAD / TEAM",
                        style={"width": W["lead_team"], "font-weight": "bold", "color": "#7a7a7a"},
                    ),
                    html.Small(
                        "MENTORS",
                        style={"width": W["mentors"], "font-weight": "bold", "color": "#7a7a7a"},
                    ),
                    html.Small(
                        "OPEN TASKS",
                        style={"width": W["open_tasks"], "font-weight": "bold", "color": "#7a7a7a"},
                    ),
                    html.Small(
                        "NOTION PROGRESS",
                        style={"width": W["progress"], "font-weight": "bold", "color": "#7a7a7a"},
                    ),
                    html.Small(
                        "ACTION",
                        style={"width": W["action"], "font-weight": "bold", "color": "#7a7a7a"},
                    ),
                ],
                style={"display": "flex", "padding": "0 16px", "margin-bottom": "8px"},
            ),
            html.Div([make_project_row(row, open_task_counts) for _, row in df.iterrows()]),
        ]
    )


app.layout = html.Div(
    style={"backgroundColor": COLORS["background"], "minHeight": "100vh"},
    children=[
        dcc.Location(id="url", refresh=False),
        html.Div(
            [
                dbc.Container(
                    [
                        html.Div(
                            [
                                html.Div(
                                    [
                                        html.H4(
                                            "2026 AI Lab Dashboard",
                                            style={
                                                "color": "white",
                                                "font-weight": "900",
                                                "margin": 0,
                                                "font-size": "1.25rem",
                                            },
                                        ),
                                    ]
                                ),
                                html.Small(
                                    f"{len(PROJECTS_DF)} projects loaded"
                                    if DATA_ERROR is None
                                    else "Data unavailable",
                                    style={"color": "rgba(255,255,255,0.85)", "font-size": "0.78rem"},
                                ),
                            ],
                            className="d-flex justify-content-between align-items-center",
                        )
                    ],
                    fluid=True,
                )
            ],
            style={
                "backgroundColor": COLORS["primary"],
                "padding": "0.55rem 0",
                "marginBottom": "1rem",
            },
        ),
        dbc.Container(id="page-content", fluid=True, style={"padding": "0 2rem"}),
    ],
)


@app.callback(Output("page-content", "children"), Input("url", "pathname"))
def render_page(pathname: str | None) -> html.Div | dbc.Container:
    if DATA_ERROR is not None:
        return render_data_error(DATA_ERROR)

    current_path = pathname or "/"
    if current_path.startswith("/project/"):
        slug = current_path.split("/")[-1]
        if not slug.isdigit():
            return render_not_found()

        project_id = int(slug)
        matches = PROJECTS_DF[PROJECTS_DF["id"] == project_id]
        if matches.empty:
            return render_not_found()
        return render_project_detail(matches.iloc[0])

    return render_dashboard(PROJECTS_DF)


@app.callback(
    Output("interaction-history-list", "children"),
    Output("interaction-save-status", "children"),
    Output("interaction-input", "value"),
    Output("interaction-store", "data"),
    Output("interaction-raw-view", "children"),
    Input("add-interaction-btn", "n_clicks", allow_optional=True),
    Input("url", "pathname"),
    State("interaction-input", "value", allow_optional=True),
    prevent_initial_call=True,
)
def handle_interactions(
    n_clicks: int | None,
    pathname: str | None,
    content: str | None,
) -> tuple[html.Div | html.P, str, str, list[dict[str, str]], html.Div | html.P]:
    current_path = pathname or "/"
    if not current_path.startswith("/project/"):
        return html.Div(), "", content or "", [], html.Div()

    slug = current_path.split("/")[-1]
    if not slug.isdigit():
        not_found = html.P("Project not found.", className="text-muted mb-0")
        return not_found, "", content or "", [], not_found

    project_id = int(slug)
    trimmed_content = clean_text(content)

    if n_clicks and trimmed_content:
        add_interaction(project_id, trimmed_content)
        interactions = fetch_project_interactions(project_id)
        latest = interactions[0]
        return (
            render_interaction_history_items(interactions),
            "Interaction saved.",
            "",
            interactions,
            render_raw_interaction_view(latest),
        )

    interactions = fetch_project_interactions(project_id)
    placeholder = (
        html.P("Paste content into the box before saving.", className="text-muted mb-0")
        if n_clicks
        else render_raw_interaction_view(None)
    )
    return (
        render_interaction_history_items(interactions),
        "Paste content into the box before saving." if n_clicks else "",
        content or "",
        interactions,
        placeholder,
    )


@app.callback(
    Output("task-editor-view", "children"),
    Output("selected-task-store", "data"),
    Input({"type": "task-row", "index": dash.ALL}, "n_clicks", allow_optional=True),
    Input("close-task-editor", "n_clicks", allow_optional=True),
    State("task-store", "data", allow_optional=True),
    prevent_initial_call=True,
)
def handle_task_selection(
    n_clicks: list[int] | None,
    close_clicks: int | None,
    tasks: list[dict[str, str]] | None,
) -> tuple[html.Div, dict[str, str] | None]:
    if not tasks:
        return render_task_editor(None), None

    ctx = dash.callback_context
    if not ctx.triggered:
        return render_task_editor(None), None

    triggered_id = ctx.triggered_id
    if triggered_id == "close-task-editor":
        if not close_clicks:
            return dash.no_update, dash.no_update
        return render_task_editor(None), None

    if not isinstance(triggered_id, dict):
        return render_task_editor(None), None

    selected_index = int(triggered_id["index"])
    if selected_index >= len(tasks):
        return render_task_editor(None), None
    if not n_clicks or selected_index >= len(n_clicks) or not n_clicks[selected_index]:
        return dash.no_update, dash.no_update

    selected_task = tasks[selected_index]
    return render_task_editor(selected_task), selected_task


@app.callback(
    Output("page-content", "children", allow_duplicate=True),
    Input("save-task-btn", "n_clicks", allow_optional=True),
    Input("delete-task-btn", "n_clicks", allow_optional=True),
    State("selected-task-store", "data", allow_optional=True),
    State("task-editor-status", "value", allow_optional=True),
    State("task-editor-comments", "value", allow_optional=True),
    State("url", "pathname"),
    prevent_initial_call=True,
)
def handle_task_updates(
    save_clicks: int | None,
    delete_clicks: int | None,
    selected_task: dict[str, str] | None,
    status: str | None,
    comments: str | None,
    pathname: str | None,
) -> html.Div | dbc.Container:
    if not selected_task:
        return dash.no_update

    ctx = dash.callback_context
    if not ctx.triggered:
        return dash.no_update

    triggered_id = ctx.triggered_id
    if triggered_id == "delete-task-btn":
        if not delete_clicks:
            return dash.no_update
        delete_task(selected_task["task_id"])
    elif triggered_id == "save-task-btn":
        if not save_clicks:
            return dash.no_update
        update_task(
            selected_task["task_id"],
            status or "open",
            comments or "",
        )
    else:
        return dash.no_update

    current_path = pathname or "/"
    if current_path.startswith("/project/"):
        slug = current_path.split("/")[-1]
        if slug.isdigit():
            project_id = int(slug)
            matches = PROJECTS_DF[PROJECTS_DF["id"] == project_id]
            if not matches.empty:
                return render_project_detail(matches.iloc[0])

    return render_dashboard(PROJECTS_DF)


@app.callback(
    Output("page-content", "children", allow_duplicate=True),
    Input("add-manual-task-btn", "n_clicks", allow_optional=True),
    State("manual-task-input", "value", allow_optional=True),
    State("manual-task-comments", "value", allow_optional=True),
    State("url", "pathname"),
    prevent_initial_call=True,
)
def handle_manual_task_create(
    n_clicks: int | None,
    description: str | None,
    comments: str | None,
    pathname: str | None,
) -> html.Div | dbc.Container:
    if not n_clicks:
        return dash.no_update

    task_description = clean_text(description)
    if not task_description:
        return dash.no_update

    current_path = pathname or "/"
    if current_path.startswith("/project/"):
        slug = current_path.split("/")[-1]
        if slug.isdigit():
            project_id = int(slug)
            add_manual_task(project_id, task_description, clean_text(comments))
            matches = PROJECTS_DF[PROJECTS_DF["id"] == project_id]
            if not matches.empty:
                return render_project_detail(matches.iloc[0])

    return render_dashboard(PROJECTS_DF)


@app.callback(
    Output("interaction-raw-view", "children", allow_duplicate=True),
    Input("close-interaction-view", "n_clicks", allow_optional=True),
    Input({"type": "interaction-row", "index": dash.ALL}, "n_clicks", allow_optional=True),
    State("interaction-store", "data", allow_optional=True),
    prevent_initial_call=True,
)
def show_raw_interaction(
    close_clicks: int | None,
    n_clicks: list[int] | None,
    interactions: list[dict[str, str]] | None,
) -> html.Div | html.P:
    if not interactions:
        return html.P("No interactions recorded yet.", className="text-muted mb-0")

    ctx = dash.callback_context
    if not ctx.triggered:
        return render_raw_interaction_view(None)

    triggered_id = ctx.triggered_id
    if triggered_id == "close-interaction-view":
        return render_raw_interaction_view(None)
    if not isinstance(triggered_id, dict):
        return render_raw_interaction_view(None)

    selected_index = int(triggered_id["index"])
    if selected_index >= len(interactions):
        return html.P("Interaction not found.", className="text-muted mb-0")

    selected = interactions[selected_index]
    return render_raw_interaction_view(selected)


if __name__ == "__main__":
    app.run(debug=True)
