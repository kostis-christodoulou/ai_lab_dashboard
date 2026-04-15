# AI Lab Dashboard

Flask app for tracking AI Lab teams, interactions, and tasks.

This app is configured to use MotherDuck for persistence.

## Local Run

```bash
uv sync
uv run python app.py
```

The app runs locally with Flask's default dev server.

## Data Sources

- `projects.csv`: source of truth for project/team data
- MotherDuck: stores `projects`, `interactions`, and `tasks`
- `notion_sources.json`: maps Notion workspaces/databases to project ids for sync

## Local Environment

Create a local `.env` file:

```env
FLASK_SECRET_KEY=replace_me
MOTHERDUCK_TOKEN=your_token_here
MOTHERDUCK_DB=ai_lab_dashboard
AZURE_SSO_ENABLED=false
AZURE_CLIENT_ID=your_app_registration_client_id
AZURE_CLIENT_SECRET=your_app_registration_client_secret
AZURE_TENANT_ID=your_entra_tenant_id
AZURE_REDIRECT_URI=http://127.0.0.1:5000/auth/callback
ALLOWED_LOGIN_EMAILS=kchristodoulou@london.edu
```

## MotherDuck Requirement

The app requires these environment variables:

- `FLASK_SECRET_KEY`
- `MOTHERDUCK_TOKEN`
- `MOTHERDUCK_DB=ai_lab_dashboard`
- `AZURE_SSO_ENABLED=false` if you want the app open without login for now
- `AZURE_CLIENT_ID`
- `AZURE_CLIENT_SECRET`
- `AZURE_TENANT_ID`
- `AZURE_REDIRECT_URI`
- `ALLOWED_LOGIN_EMAILS`

## Azure SSO

This app uses Microsoft Entra ID via Azure SSO.

Configure an App Registration with:

- Platform type: `Web`
- Redirect URI for local dev: `http://127.0.0.1:5000/auth/callback`
- Redirect URI for Render: `https://your-render-domain/auth/callback`

Only email addresses listed in `ALLOWED_LOGIN_EMAILS` can access the app after Azure sign-in succeeds.

## Notion Task Sync

Use `pull_notion.py` to inspect or sync external Notion task boards into the shared `tasks` table.

```bash
uv run python pull_notion.py --source compass
uv run python pull_notion.py --source compass --sync
```

To add another team:

1. Add a new object to `notion_sources.json`
2. Add the corresponding token env var to `.env` or Render
3. Run `pull_notion.py --source <key> --sync`

## Migrate Local Data To MotherDuck

If you have an existing local DuckDB file and want to migrate it:

From the `dashboard/` folder:

```bash
export MOTHERDUCK_TOKEN='your_token_here'
export MOTHERDUCK_DB='ai_lab_dashboard'
uv run python migrate_to_motherduck.py
```

## Render Deployment

This repo includes `render.yaml`.

Render should use:

- Build command: `pip install uv && uv sync --frozen`
- Start command: `uv run gunicorn app:app`

### Render Environment Variables

Set these in the Render dashboard for the web service:

- `MOTHERDUCK_DB=ai_lab_dashboard`
- `MOTHERDUCK_TOKEN=<your MotherDuck token>`
- `FLASK_SECRET_KEY=<long random string>`
- `AZURE_CLIENT_ID=<app registration client id>`
- `AZURE_CLIENT_SECRET=<app registration client secret>`
- `AZURE_TENANT_ID=<entra tenant id>`
- `AZURE_REDIRECT_URI=https://your-render-domain/auth/callback`
- `ALLOWED_LOGIN_EMAILS=kchristodoulou@london.edu`

Do not commit the token to git.

## Notes

- `projects.csv` is version-controlled
- `.env` is ignored by git
- MotherDuck is the only supported persistent store for this app
- Task records now keep audit fields for creation, update, completion, and soft deletion
