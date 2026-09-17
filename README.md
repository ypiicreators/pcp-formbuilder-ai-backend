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
