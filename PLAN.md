# SDM Kanban Board — Design & Architecture

## Why this exists

Shared kanban board for the Cisco SDM Escalation Team to track ideas, prioritize work, and coordinate multi-person efforts. The Shared Progress bucket includes CEC-ID tracking so everyone can see who's working on what.

## Tech Choices

| Option | Verdict | Reason |
|--------|---------|--------|
| Flask + Docker (chosen) | ✅ | Matches all 4 existing webapps; full API for CLI/bot integrations |
| Notion / Trello | ✗ | No CEC-ID concept; can't integrate with internal tools |
| GitHub Projects | ✗ | Clunky for lightweight idea tracking; no CEC-ID field |
| Static HTML + localStorage | ✗ | Single-user only; no shared state |

## Architecture

```
webapps/sdm-kanban/
  app.py              Flask backend — all routes + file-locking JSON persistence
  requirements.txt    flask, gunicorn, filelock
  Dockerfile          python:3.11-slim · port 9999 · gunicorn 2w+4t
  docker-compose.yml  named volume, healthcheck, restart: unless-stopped
  templates/
    index.html        Jinja2 board — 4 columns, modals, SortableJS CDN
  static/
    css/style.css     Dark/light theme (same palette as hf-query-tool)
    js/board.js       Drag-drop, modals, API calls, theme toggle
  data/
    cards.json        Persistent card store (Docker volume on prod)
```

## Data Model

```json
{
  "id": "card_<unix>_<6hex>",
  "title": "Short description",
  "description": "Details, context, SR numbers...",
  "bucket": "ideas | in-progress | shared-progress | complete",
  "priority": 0,
  "cec_ids": ["kamancha", "jdoe"],
  "created_at": "2026-04-22T10:00:00Z",
  "updated_at": "2026-04-22T10:00:00Z"
}
```

- `priority` — integer; only meaningful in Ideas (0 = top). Renumbered on every reorder.
- `cec_ids` — only populated in shared-progress. Cleared when card leaves that bucket.
- Thread safety: `filelock` wraps all writes (guards against 2-worker Gunicorn race).

## API

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/` | Render board |
| GET | `/api/cards` | All cards |
| POST | `/api/cards` | Create card |
| PUT | `/api/cards/<id>` | Edit title/description |
| DELETE | `/api/cards/<id>` | Delete card |
| PUT | `/api/cards/<id>/move` | Move to different bucket |
| POST | `/api/cards/reorder-bulk` | Reorder Ideas after drag |
| POST | `/api/cards/<id>/cec` | Add CEC-ID |
| DELETE | `/api/cards/<id>/cec/<cec_id>` | Remove CEC-ID |
| POST | `/api/cards/quick` | Quick-add: `{title}` only → top of Ideas |
| GET | `/api/health` | Docker healthcheck |

## Frontend

- 4-column CSS Grid (responsive: 2-col @ 1024px, 1-col @ 600px)
- **SortableJS** (CDN) — reorder within Ideas, drag between any columns
- Dropping a card into Shared Progress auto-opens modal to add CEC-IDs
- Click any card → modal for view/edit/delete; priority badges (#1, #2…) in Ideas
- Dark theme default; `☀ Light` toggle stored in localStorage
- Keyboard: `n` = new card, `Esc` = close modal

## Deployment

```bash
# Build and start
docker-compose up -d --build

# View logs
docker logs -f sdm-kanban

# Stop
docker-compose down
```

Data persists in Docker volume `sdm-kanban-data`.

## Ways to Add Ideas

```bash
# 1. Web UI — click "+ New Card" or press 'n'

# 2. curl one-liner
curl -s -X POST http://server:9999/api/cards/quick \
  -H 'Content-Type: application/json' \
  -d '{"title": "My idea", "description": "Optional details"}'

# 3. Shell alias — add to ~/.zshrc
kanban-add() {
  curl -s -X POST http://server:9999/api/cards/quick \
    -H 'Content-Type: application/json' \
    -d "{\"title\": \"$*\"}" | python3 -m json.tool
}
# Usage: kanban-add Fix the FMC upgrade timeout

# 4. Claude Code skill  →  /kanban-add <title>

# 5. Webex bot — !idea <title> in team space (webex-messaging MCP)
```

## Local Dev

```bash
cd webapps/sdm-kanban
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
flask run --port 9999
# open http://localhost:9999
```
