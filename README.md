# AI Lab Dashboard

Dash app for tracking AI Lab teams, interactions, and tasks.

This app is configured to use MotherDuck for persistence.

## Local Run

```bash
uv sync
uv run python app.py
```

The app runs on `http://127.0.0.1:8050/`.

## Data Sources

- `projects.csv`: source of truth for project/team data
- MotherDuck: stores `projects`, `interactions`, and `tasks`

## Local Environment

Create a local `.env` file:

```env
MOTHERDUCK_TOKEN=your_token_here
MOTHERDUCK_DB=ai_lab_dashboard
```

## MotherDuck Requirement

The app requires these environment variables:

- `MOTHERDUCK_TOKEN`
- `MOTHERDUCK_DB=ai_lab_dashboard`

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
- Start command: `uv run gunicorn app:server`

### Render Environment Variables

Set these in the Render dashboard for the web service:

- `MOTHERDUCK_DB=ai_lab_dashboard`
- `MOTHERDUCK_TOKEN=<your MotherDuck token>`

Do not commit the token to git.

## Notes

- `projects.csv` is version-controlled
- `.env` is ignored by git
- MotherDuck is the only supported persistent store for this app
