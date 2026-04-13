import dash
from dash import dcc, html, Input, Output, State
import dash_bootstrap_components as dbc
import pandas as pd
import json
import re
from collections import Counter

# --- 1. MENTOR DEFINITIONS ---
mentor_team_1 = "Zahra (product) +\nRamakrishnan (tech)"

# --- 2. DATA ---
raw_data = [
    {
        "id": 16,
        "name": "BrandOS",
        "lead": "Pranav Bangalore Sathyachandan",
        "lead_email": "pranavb.mam2026@london.edu",
        "raw_members": "jdamani.mam2026@london.edu; achan.mba2026@london.edu",
        "summary": "Stopping student clubs from wasting £380k on failed events via AI automation.",
        "full_problem": "LBS student clubs waste £380,000+ annually on failed events...",
        "full_success": "Working MVP deployed.",
        "support": "Commercial mentorship",
        "mentor": mentor_team_1,
    },
    {
        "id": 4,
        "name": "RecruitSmart LBS",
        "lead": "Lavanya Saberwal",
        "lead_email": "lsaberwal.mba2027@london.edu",
        "raw_members": "ritaa.mba2027@london.edu; japlin.mba2027@london.edu",
        "summary": "Personalised AI career coaching and CRM for MBA recruitment.",
        "full_problem": "LBS MBA students face high-stakes recruitment...",
        "full_success": "40+ active users.",
        "support": "API Tokens",
        "mentor": mentor_team_1,
    },
    {
        "id": 10,
        "name": "Campus Collective",
        "lead": "Emma Wang",
        "lead_email": "ewang.mba2027@london.edu",
        "raw_members": "cespiritu.mba2027@london.edu; pnayak.mba2027@london.edu",
        "summary": "AI menu management for the cafeteria and smarter EMS bidding.",
        "full_problem": "No digital touchpoint for menus.",
        "full_success": "CafeSmart app.",
        "support": "Tech mentorship",
        "mentor": mentor_team_1,
    },
    {
        "id": 3,
        "name": "Dawn",
        "lead": "Patrick Arbuthnott",
        "lead_email": "parbuthnott.mba2027@london.edu",
        "raw_members": "mbrun.mba2027@london.edu; smcdonald.mba2027@london.edu",
        "summary": "A unified inbox for all communication channels with AI hand-offs.",
        "full_problem": "Unified inbox for all channels.",
        "full_success": "Working MVP.",
        "support": "Tech mentorship",
        "mentor": mentor_team_1,
    },
    {
        "id": 5,
        "name": "EMS++",
        "lead": "Matt Bodsworth",
        "lead_email": "mbodsworth.mba2027@london.edu",
        "raw_members": "",
        "summary": "Upgrading the EMS experience with calendar planning.",
        "full_problem": "Improving EMS UX.",
        "full_success": "Updated tool.",
        "support": "API Tokens",
        "mentor": mentor_team_1,
    },
    {
        "id": 15,
        "name": "FundEd",
        "lead": "Nothando Ntombela",
        "lead_email": "nntombela.mba2027@london.edu",
        "raw_members": "mparikh.mba2027@london.edu",
        "summary": "AI marketplace for global funding sources.",
        "full_problem": "Scholarship discovery is manual.",
        "full_success": "Working MVP.",
        "support": "Tech mentorship",
        "mentor": mentor_team_1,
    },
]


def parse_team_details(row):
    all_text = f"{row['lead_email']} {row['raw_members']}"
    emails = list(
        set(re.findall(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", all_text))
    )
    degrees = []
    for e in emails:
        match = re.search(r"\.([a-zA-Z]+)(\d{4})@", e)
        if match:
            degrees.append(match.group(1).upper())
    counts = Counter(degrees)
    comp = ", ".join([f"{v} {k}" for k, v in counts.items()])
    return emails, (comp if comp else "1 LBS")


df_list = []
for item in raw_data:
    emails, comp = parse_team_details(item)
    item["all_emails"] = emails
    item["team_composition"] = comp
    df_list.append(item)
df = pd.DataFrame(df_list).sort_values(by="name")

# --- 3. APP SETUP ---
COLORS = {"primary": "#001e62", "background": "#fafafa", "white": "#ffffff"}
app = dash.Dash(
    __name__,
    external_stylesheets=[dbc.themes.BOOTSTRAP],
    suppress_callback_exceptions=True,
)

# --- 4. COLUMN STYLE DEFINITION ---
# Fixed percentages force the columns to stay as columns
COL_STYLES = {
    "project": {"width": "35%", "padding": "10px"},
    "lead": {"width": "15%", "padding": "10px"},
    "team": {"width": "10%", "padding": "10px"},
    "mentor": {"width": "12%", "padding": "10px"},
    "roadmap": {"width": "20%", "padding": "10px"},
    "action": {"width": "8%", "padding": "10px", "text-align": "center"},
}


def make_task_mini_bar(label, status, color):
    return html.Div(
        [
            html.Div(
                [
                    html.Small(label, style={"font-size": "0.55rem", "color": "#777"}),
                    html.Small(
                        status,
                        style={"font-size": "0.5rem", "font-weight": "700"},
                        className=f"text-{color}",
                    ),
                ],
                className="d-flex justify-content-between",
                style={"line-height": "1"},
            ),
            dbc.Progress(
                value=100
                if status == "Completed"
                else (40 if status == "In Progress" else 5),
                color=color,
                style={"height": "2px", "margin-bottom": "4px"},
            ),
        ]
    )


def make_project_row(row):
    # This Div uses FLEXBOX to force horizontal alignment
    return html.Div(
        style={
            "display": "flex",
            "flex-direction": "row",
            "align-items": "center",
            "background": "white",
            "border-bottom": "1px solid #eee",
            "padding": "5px 0",
        },
        children=[
            # 1. Project
            html.Div(
                style=COL_STYLES["project"],
                children=[
                    html.H6(
                        row["name"],
                        style={
                            "font-weight": "bold",
                            "margin": "0",
                            "color": COLORS["primary"],
                            "font-size": "0.9rem",
                        },
                    ),
                    html.P(
                        row["summary"],
                        style={"font-size": "0.75rem", "margin": "0", "color": "#666"},
                    ),
                ],
            ),
            # 2. Lead
            html.Div(
                style=COL_STYLES["lead"],
                children=[
                    html.Small(
                        row["lead"],
                        style={"font-weight": "600", "font-size": "0.75rem"},
                    ),
                ],
            ),
            # 3. Team
            html.Div(
                style=COL_STYLES["team"],
                children=[
                    dbc.Badge(
                        row["team_composition"],
                        color="light",
                        text_color="dark",
                        style={"font-size": "0.55rem", "border": "1px solid #ddd"},
                    ),
                ],
            ),
            # 4. Mentors
            html.Div(
                style=COL_STYLES["mentor"],
                children=[
                    html.Small(
                        row["mentor"],
                        style={
                            "font-size": "0.65rem",
                            "color": COLORS["primary"],
                            "line-height": "1.1",
                            "white-space": "pre-wrap",
                        },
                    ),
                ],
            ),
            # 5. Roadmap
            html.Div(
                style=COL_STYLES["roadmap"],
                children=[
                    make_task_mini_bar("Interviews", "Completed", "success"),
                    make_task_mini_bar("MVP Dev", "In Progress", "warning"),
                    make_task_mini_bar("AI Integration", "To Do", "secondary"),
                ],
            ),
            # 6. Action
            html.Div(
                style=COL_STYLES["action"],
                children=[
                    dbc.Button(
                        "View",
                        id={"type": "view-btn", "index": row["id"]},
                        size="sm",
                        color="dark",
                        outline=True,
                        style={"font-size": "0.6rem", "width": "100%"},
                    ),
                ],
            ),
        ],
    )


# --- 5. LAYOUT ---
app.layout = html.Div(
    style={
        "background-color": COLORS["background"],
        "min-height": "100vh",
        "font-family": "sans-serif",
    },
    children=[
        dcc.Location(id="url", refresh=False),
        # Header
        html.Div(
            [
                dbc.Container(
                    [
                        html.Div(
                            [
                                html.H4(
                                    "2026 AI Lab Dashboard",
                                    style={
                                        "color": "white",
                                        "font-weight": "900",
                                        "margin": 0,
                                    },
                                ),
                                html.Small(
                                    "LBS INNOVATION PORTAL",
                                    style={
                                        "color": "rgba(255,255,255,0.6)",
                                        "letter-spacing": "2px",
                                    },
                                ),
                            ],
                            className="d-flex justify-content-between align-items-center",
                        )
                    ],
                    fluid=True,
                )
            ],
            style={
                "background-color": COLORS["primary"],
                "padding": "1rem 0",
                "margin-bottom": "2rem",
            },
        ),
        dbc.Container(id="page-content", fluid=True, style={"padding": "0 2rem"}),
    ],
)

# --- 6. CALLBACKS ---


@app.callback(Output("page-content", "children"), [Input("url", "pathname")])
def render_page(pathname):
    if pathname.startswith("/project/"):
        pid = int(pathname.split("/")[-1])
        proj = df[df["id"] == pid].iloc[0]
        return dbc.Container(
            [
                dbc.Button(
                    "← Overview",
                    href="/",
                    color="link",
                    className="p-0 mb-3",
                    style={"color": COLORS["primary"]},
                ),
                dbc.Row(
                    [
                        dbc.Col(
                            [
                                html.H2(
                                    proj["name"],
                                    style={
                                        "font-weight": "900",
                                        "color": COLORS["primary"],
                                    },
                                ),
                                html.P(proj["summary"], className="lead text-muted"),
                                html.Div(
                                    [
                                        html.H5(
                                            "The Full Submission",
                                            style={"font-weight": "bold"},
                                        ),
                                        html.P(
                                            proj["full_problem"],
                                            style={
                                                "white-space": "pre-wrap",
                                                "font-size": "1rem",
                                            },
                                        ),
                                    ],
                                    className="bg-white p-4 border rounded shadow-sm",
                                ),
                            ],
                            width=8,
                        ),
                        dbc.Col(
                            [
                                dbc.Card(
                                    [
                                        dbc.CardBody(
                                            [
                                                html.Small(
                                                    "Lead",
                                                    className="text-muted d-block",
                                                ),
                                                html.P(
                                                    proj["lead"],
                                                    style={"font-weight": "bold"},
                                                ),
                                                html.Small(
                                                    "All Emails",
                                                    className="text-muted d-block",
                                                ),
                                                html.Div(
                                                    [
                                                        html.P(
                                                            e,
                                                            style={
                                                                "font-size": "0.75rem",
                                                                "margin": "0",
                                                            },
                                                        )
                                                        for e in proj["all_emails"]
                                                    ]
                                                ),
                                            ]
                                        )
                                    ]
                                )
                            ],
                            width=4,
                        ),
                    ]
                ),
            ],
            fluid=True,
        )

    # Main List View (FORCED COLUMNS)
    return html.Div(
        [
            # Header Row
            html.Div(
                style={
                    "display": "flex",
                    "flex-direction": "row",
                    "padding": "10px 0",
                    "border-bottom": "2px solid #ddd",
                    "background": "#eee",
                    "border-radius": "4px 4px 0 0",
                },
                children=[
                    html.Div("PROJECT & SUMMARY", style=COL_STYLES["project"]),
                    html.Div("LEAD", style=COL_STYLES["lead"]),
                    html.Div("TEAM", style=COL_STYLES["team"]),
                    html.Div("MENTORS", style=COL_STYLES["mentor"]),
                    html.Div("ROADMAP (NOTION FEED)", style=COL_STYLES["roadmap"]),
                    html.Div("ACTION", style=COL_STYLES["action"]),
                ],
                className="small fw-bold text-muted",
            ),
            # Data Rows
            html.Div(
                [make_project_row(row) for _, row in df.iterrows()],
                style={
                    "border": "1px solid #eee",
                    "border-top": "none",
                    "border-radius": "0 0 4px 4px",
                },
            ),
        ]
    )


@app.callback(
    Output("url", "pathname", allow_duplicate=True),
    Input({"type": "view-btn", "index": dash.ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def navigate_to_project(n_clicks):
    ctx = dash.callback_context
    if not ctx.triggered:
        return dash.no_update
    button_id = ctx.triggered[0]["prop_id"].split(".")[0]
    return f"/project/{json.loads(button_id)['index']}"


if __name__ == "__main__":
    app.run(debug=True)
