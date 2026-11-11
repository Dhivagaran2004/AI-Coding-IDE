# AI Coding IDE

## Backend migrations

Run database migrations from the `Backend` directory after updating the
application and before starting the backend. `Base.metadata.create_all()` does
not alter existing tables, so it cannot replace this step:

```text
python -m alembic upgrade head
```

The migration configuration reads the backend environment (including
`DATABASE_URL` and optional `DB_SCHEMA`). It creates the persistent AI
conversation/message and agent-task tables; user and project tables must
already exist.

## Persistent AI and Git APIs

- `POST /ai/chat` remains the regular JSON chat endpoint.
- `POST /ai/chat/stream` streams `token`, `done`, and `error` events as
  Server-Sent Events. Aborting the HTTP request stops generation.
- `GET`, `POST`, `PATCH`, and `DELETE /ai/conversations` provide
  owner-scoped conversation management. Conversation message history is loaded
  server-side when `conversation_id` is supplied to chat.
- `GET /ai/agent/tasks` lists the current user's agent tasks. Interrupted active
  tasks recover as `paused` and require explicit reinspection before continuing.
- `POST /ai/agent/tasks/{task_id}/undo` restores task changes only when each
  current file hash still matches the AI-produced version.
- `GET /projects/{project_id}/git/status` and
  `GET /projects/{project_id}/git/diff` expose read-only workspace Git state.
- `GET /health` is a liveness check; `GET /health/ready` reports database and
  AI-provider configuration readiness without exposing credentials.

The Git APIs do not commit, push, reset, or otherwise mutate repository state.

## AI provider configuration

Set `LLM_PROVIDER=groq`, `GROQ_API_KEY`, and `GROQ_MODEL` in
`Backend/App/.env` to enable Groq chat. The backend uses Groq's OpenAI-compatible
chat completions endpoint and keeps the API key server-side. `openai` is already
listed in `Backend/requirements.txt` as the compatible client SDK. Provider
requests have a 60-second timeout; application-level retries are bounded by
`AI_MAX_RETRIES` so an agent task cannot wait indefinitely on a planning call.

Repository embeddings remain optional and can use `EMBEDDING_API_KEY` for the
existing Hugging Face embedding endpoint. Existing deployments may continue to
use `LLM_API_KEY` for embeddings during migration.

## Docker sandbox execution

- `POST /projects/{project_id}/sandbox/execute` runs project commands inside an isolated Docker workspace.
- The sandbox uses a non-root container user, disables outbound network access, caps CPU/RAM/process limits, and enforces a 120-second maximum timeout.
- Successful sandbox runs sync filesystem changes back to the project workspace; failed runs leave the original workspace untouched.
- The AI agent uses the same sandbox service and database-backed project files. Read-only file tools run directly against the selected, owner-scoped project; file changes and sandbox commands require approval before applying.
- Agent sandbox commands are restricted to approved project checks, including `python <project-file.py>`, pytest, selected npm tasks, and read-only Git status/diff. There is no host-shell fallback.
- `AI_AGENT_MAX_TOOL_CALLS` limits the model's tool loop to 1–20 calls (default 8). Project-file database records remain authoritative; successful sandbox file changes are synchronized to those records and the workspace mirror used by terminal/Git services.