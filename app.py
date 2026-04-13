import dash
from dash import dcc, html, Input, Output, State
import dash_bootstrap_components as dbc
import pandas as pd
import json
import re
from collections import Counter

# --- 1. MENTOR DEFINITIONS ---
mentor_team_1 = "Zahra (product) + Ramakrishnan (tech)"
mentor_team_2 = "Rhea (product) + Akshay (tech)"

# --- 2. DATA PREPARATION ---
raw_data = [
    {
        "id": 16,
        "name": "BrandOS",
        "lead": "Pranav Bangalore Sathyachandan",
        "lead_email": "pranavb.mam2026@london.edu",
        "raw_members": "Jash Damani, jdamani.mam2026@london.edu; Angela Chan, achan.mba2026@london.edu",
        "summary": "Stopping student clubs from wasting £380k on failed events via AI automation.",
        "full_problem": "LBS student clubs waste £380,000+ annually on failed events due to 47% average flake rates and broken planning processes...",
        "full_success": "Working MVP deployed with 4 AI agents. 3-4 student clubs testing.",
        "support": "Commercial mentorship",
        "mentor": mentor_team_1,
    },
    {
        "id": 4,
        "name": "RecruitSmart LBS",
        "lead": "Lavanya Saberwal",
        "lead_email": "lsaberwal.mba2027@london.edu",
        "raw_members": "Rita Antunes ritaa.mba2027@london.edu; Jamie Aplin japlin.mba2027@london.edu",
        "summary": "Personalised AI career coaching and CRM for high-stakes MBA recruitment.",
        "full_problem": "LBS MBA students face a high-stakes recruitment cycle managed with a patchwork of spreadsheets.",
        "full_success": "40+ active users. 5% increase in CV-to-interview conversion.",
        "support": "API Tokens",
        "mentor": mentor_team_1,
    },
    {
        "id": 12,
        "name": "LBS Compass",
        "lead": "Burcu Magemizoglu",
        "lead_email": "bmagemizoglu.mba2027@london.edu",
        "raw_members": "Ana Vitoria Aragao Andrade: anavitoriaa.mba2027@london.edu",
        "summary": "An AI assistant that helps new students prioritize opportunities from day one.",
        "full_problem": "LBS students struggle to know which options to pick. Lost time in reactive mode.",
        "full_success": "Working prototype of an AI assistant.",
        "support": "Technical mentorship",
        "mentor": mentor_team_1,
    },
    {
        "id": 10,
        "name": "Campus Collective",
        "lead": "Emma Wang",
        "lead_email": "ewang.mba2027@london.edu",
        "raw_members": "Caitlyn Espiritu: cespiritu.mba2027@london.edu",
        "summary": "AI menu management for the cafeteria and a smarter EMS elective bidding system.",
        "full_problem": "No digital touchpoint for cafeteria menus. Frustrating elective bidding (EMS).",
        "full_success": "CafeSmart app and Super EMS prototype.",
        "support": "Technical mentorship",
        "mentor": mentor_team_1,
    },
    {
        "id": 15,
        "name": "FundEd",
        "lead": "Nothando Ntombela",
        "lead_email": "nntombela.mba2027@london.edu",
        "raw_members": "Misri Parikh mparikh.mba2027@london.edu",
        "summary": "AI marketplace connecting researchers and students with global funding sources.",
        "full_problem": "Discovery of scholarships is manual and fragmented.",
        "full_success": "Working MVP with AI-assisted search.",
        "support": "Technical mentorship",
        "mentor": mentor_team_1,
    },
    {
        "id": 1,
        "name": "The Wayfinders",
        "lead": "Simon Scott",
        "lead_email": "sscott.mifpt2025@london.edu",
        "raw_members": "",
        "summary": "A frictionless map app to end the confusion of finding rooms on campus.",
        "full_problem": "High friction for people heading to campus not being certain where the room location is.",
        "full_success": "A free mobile app that shows exactly where a room is.",
        "support": "Technical mentorship",
        "mentor": mentor_team_1,
    },
    {
        "id": 5,
        "name": "EMS++",
        "lead": "Matt Bodsworth",
        "lead_email": "mbodsworth.mba2027@london.edu",
        "raw_members": "",
        "summary": "Upgrading the EMS experience with calendar planning and concentration tracking.",
        "full_problem": "Improving EMS UX whilst keeping the LBS Staff effort as minimal as possible.",
        "full_success": "Updated tool with calendar planning.",
        "support": "API Tokens",
        "mentor": mentor_team_1,
    },
    {
        "id": 3,
        "name": "Dawn",
        "lead": "Patrick Arbuthnott",
        "lead_email": "parbuthnott.mba2027@london.edu",
        "raw_members": "Maxime Brun mbrun.mba2027@london.edu",
        "summary": "A unified inbox for all communication channels with autonomous AI hand-offs.",
        "full_problem": "Building a unified inbox to connect all communications channels.",
        "full_success": "Having a working MVP.",
        "support": "Technical mentorship",
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

# --- 4. FLEXBOX WIDTHS ---
W = {
    "project": "40%",
    "lead": "15%",
    "team": "12%",
    "mentors": "18%",
    "rdmp": "8%",
    "action": "7%",
}

# --- 5. UI COMPONENTS ---


def make_task_mini_bar(label, status, color):
    return html.Div(
        [
            html.Div(
                [
                    html.Small(label, style={"font-size": "0.6rem", "color": "#777"}),
                    html.Small(
                        status,
                        style={"font-size": "0.55rem", "font-weight": "700"},
                        className=f"text-{color}",
                    ),
                ],
                className="d-flex justify-content-between",
            ),
            dbc.Progress(
                value=100 if status == "Done" else 40,
                color=color,
                style={"height": "3px"},
            ),
        ],
        style={"margin-bottom": "2px"},
    )


def make_project_row(row):
    return html.Div(
        [
            # Project & Summary
            html.Div(
                [
                    html.H6(
                        row["name"],
                        style={
                            "font-weight": "bold",
                            "margin-bottom": "0",
                            "color": COLORS["primary"],
                        },
                    ),
                    html.P(
                        row["summary"],
                        style={
                            "font-size": "0.75rem",
                            "margin-bottom": "0",
                            "color": "#666",
                            "white-space": "nowrap",
                            "overflow": "hidden",
                            "text-overflow": "ellipsis",
                        },
                    ),
                ],
                style={"width": W["project"], "padding-right": "15px"},
            ),
            # Lead
            html.Div(
                html.Small(row["lead"], style={"font-weight": "600"}),
                style={"width": W["lead"]},
            ),
            # Team
            html.Div(
                dbc.Badge(
                    row["team_composition"],
                    color="light",
                    text_color="dark",
                    style={"font-size": "0.6rem", "border": "1px solid #ddd"},
                ),
                style={"width": W["team"]},
            ),
            # Mentors
            html.Div(
                html.Small(
                    row["mentor"],
                    style={"font-size": "0.7rem", "color": COLORS["primary"]},
                ),
                style={"width": W["mentors"]},
            ),
            # Roadmap
            html.Div(
                [make_task_mini_bar("MVP", "Active", "warning")],
                style={"width": W["rdmp"]},
            ),
            # Action
            html.Div(
                dbc.Button(
                    "View",
                    id={"type": "view-btn", "index": row["id"]},
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
            "background-color": "white",
            "padding": "12px 20px",
            "border-radius": "8px",
            "border": "1px solid #eee",
            "margin-bottom": "10px",
        },
        className="shadow-sm",
    )


# --- 6. LAYOUT ---
app.layout = html.Div(
    style={"background-color": COLORS["background"], "min-height": "100vh"},
    children=[
        dcc.Location(id="url", refresh=False),
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
        dbc.Container(id="page-content", fluid=True, style={"padding": "0 3rem"}),
    ],
)

# --- 7. CALLBACKS ---


@app.callback(Output("page-content", "children"), [Input("url", "pathname")])
def render_page(pathname):
    if pathname.startswith("/project/"):
        pid = int(pathname.split("/")[-1])
        proj = df[df["id"] == pid].iloc[0]
        return dbc.Container(
            [
                dbc.Button(
                    "← Cohort Overview",
                    href="/",
                    color="link",
                    className="p-0 mb-4",
                    style={"color": COLORS["primary"]},
                ),
                dbc.Row(
                    [
                        dbc.Col(
                            [
                                html.H1(
                                    proj["name"],
                                    style={
                                        "font-weight": "900",
                                        "color": COLORS["primary"],
                                    },
                                ),
                                html.P(
                                    proj["summary"], className="lead text-muted mb-4"
                                ),
                                html.Div(
                                    [
                                        html.H5(
                                            "The Problem Submission",
                                            style={"font-weight": "bold"},
                                        ),
                                        html.P(
                                            proj["full_problem"],
                                            style={
                                                "white-space": "pre-wrap",
                                                "line-height": "1.7",
                                                "font-size": "1rem",
                                            },
                                        ),
                                        html.Hr(className="my-5"),
                                        html.H5(
                                            "8-Week Success Metric",
                                            style={"font-weight": "bold"},
                                        ),
                                        html.P(
                                            proj["full_success"],
                                            style={
                                                "white-space": "pre-wrap",
                                                "line-height": "1.7",
                                                "font-size": "1rem",
                                            },
                                        ),
                                    ],
                                    className="bg-white p-5 border shadow-sm rounded-3",
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
                                                    proj["lead"],
                                                    style={"font-weight": "700"},
                                                ),
                                                html.Small(
                                                    "Mentor Team",
                                                    className="text-muted d-block",
                                                ),
                                                html.P(
                                                    proj["mentor"],
                                                    style={
                                                        "font-weight": "700",
                                                        "color": COLORS["primary"],
                                                    },
                                                ),
                                                html.Hr(),
                                                html.Small(
                                                    "Team Composition",
                                                    className="text-muted d-block",
                                                ),
                                                html.P(
                                                    proj["team_composition"],
                                                    style={"font-weight": "600"},
                                                ),
                                                html.Small(
                                                    "Participant Emails",
                                                    className="text-muted d-block",
                                                ),
                                                html.Div(
                                                    [
                                                        html.P(
                                                            e,
                                                            style={
                                                                "font-size": "0.75rem",
                                                                "margin-bottom": "2px",
                                                            },
                                                        )
                                                        for e in proj["all_emails"]
                                                    ]
                                                ),
                                            ]
                                        ),
                                    ],
                                    className="border-0 shadow-sm",
                                )
                            ],
                            width=4,
                        ),
                    ]
                ),
            ],
            fluid=True,
        )

    # Main List View (FORCED FLEXBOX HEADERS)
    return html.Div(
        [
            html.Div(
                [
                    html.Small(
                        "PROJECT & SUMMARY",
                        style={
                            "width": W["project"],
                            "font-weight": "bold",
                            "color": "#999",
                        },
                    ),
                    html.Small(
                        "LEAD",
                        style={
                            "width": W["lead"],
                            "font-weight": "bold",
                            "color": "#999",
                        },
                    ),
                    html.Small(
                        "TEAM",
                        style={
                            "width": W["team"],
                            "font-weight": "bold",
                            "color": "#999",
                        },
                    ),
                    html.Small(
                        "MENTORS",
                        style={
                            "width": W["mentors"],
                            "font-weight": "bold",
                            "color": "#999",
                        },
                    ),
                    html.Small(
                        "RDMP",
                        style={
                            "width": W["rdmp"],
                            "font-weight": "bold",
                            "color": "#999",
                        },
                    ),
                    html.Small(
                        "ACTION",
                        style={
                            "width": W["action"],
                            "font-weight": "bold",
                            "color": "#999",
                        },
                    ),
                ],
                style={"display": "flex", "padding": "0 20px", "margin-bottom": "10px"},
            ),
            html.Div([make_project_row(row) for _, row in df.iterrows()]),
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
