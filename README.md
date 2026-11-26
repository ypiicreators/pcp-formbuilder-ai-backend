# PCP AI Form Builder — Backend

AI-assisted backend that generates and edits Form Builder configurations for the
PCP Admin Portal. It exposes a single endpoint that accepts a natural-language
description, an uploaded document, or an existing form plus an edit instruction,
and returns a validated, `loadFromJSON`-ready form.

## Stack

- **FastAPI** — HTTP API
- **Pydantic / pydantic-settings** — schemas + env config
- **LangGraph** — orchestration workflow (router → generate → validate → repair → finalize)
- **httpx** — LLM provider HTTP calls
- Provider-agnostic LLM interface (default adapter: Azure OpenAI)

## Setup

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in real values
```

## Run

```bash
uvicorn app.main:app --reload
.venv/bin/uvicorn app.main:app --reload --port 8000
```

- API docs: http://localhost:8000/docs
- Health:   http://localhost:8000/api/health
- KB (Mongo required): http://localhost:8000/api/kb/entries

Set `MONGO_URI` and `MONGO_DB` in `.env`. On first KB request the database is
indexed and seeded with the same starter rules as the admin mock (geo district
API, PIN/phone templates, policies). Collections: `kb_entries`, `kb_entry_versions`,
`kb_releases`, `kb_meta`.

Admin portal (local): leave `VITE_AI_API_BASE_URL` empty so Vite proxies `/kb` →
`/api/kb`, and set `VITE_KB_USE_MOCK=false` in the admin `.env` to use this API.

## Knowledge Base API (`/api/kb`)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/entries` | List (search, kind, status, pagination) |
| POST | `/entries` | Create draft entry |
| PUT | `/entries/{key}` | Save new version (`expectedVersion`, 409 if stale) |
| POST | `/entries/{key}/archive` \| `/restore` | Archive / restore |
| GET | `/entries/{key}/versions` | Version history |
| GET | `/releases/pending` | Unpublished changes |
| GET | `/releases` | Release history |
| POST | `/releases` | Publish `{ note, keys? }` (partial publish supported) |
| POST | `/releases/{id}/rollback` | Roll back to a prior release |

Optional header `X-Kb-Actor` sets `updatedBy` / `publishedBy` (default `admin`).

**Form AI** loads the **current published release** on each generate/edit assist call:
policies and payloads are injected into the user prompt; `api_endpoint` / `template`
entries are **bound** onto matching fields after the LLM (removes static options,
applies datasource, resolves `{state}` → state field id).

## API

`POST /api/form-ai/generate` (multipart form-data)

| Field       | Modes                        | Required                        |
|-------------|------------------------------|---------------------------------|
| `mode`      | all                          | yes (`generate_nl`/`generate_doc`/`edit_json`) |
| `prompt`    | all                          | required for nl + edit; optional for doc |
| `base_json` | edit_json                    | required for edit_json          |
| `file`      | generate_doc                 | required for generate_doc       |

Response (200): `{ "form": {...}, "warnings": [...], "diff": [...]? }`
Response (422): `{ "errors": [...] }`

> The generate endpoint is currently a **stub** (validates the contract and
> returns a canned form). LLM orchestration is wired in a later step.
