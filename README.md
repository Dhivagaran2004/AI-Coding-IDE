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