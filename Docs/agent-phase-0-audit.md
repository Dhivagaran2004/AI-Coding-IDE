# Agent Upgrade — Phase 0 Architecture Audit

**Scope:** Read-only audit of the existing agent implementation and the Employee Management example. No application source was changed.

## Executive summary

The current agent is a persistent, approval-gated planner with structured requirement/task plans, repository intelligence, dependency-aware task scheduling, per-task history, and optional Docker validation. Applied file changes remain distinct from completed work: structured plan tasks require explicit verification notes or successful approved validation before completion.

The Employee example is consistent with those gaps: the agent can propose a plausible small set of files, but there is no structured requirement or acceptance-criteria contract to require persistence, CRUD coverage, or tests; plan strings and file actions are not cross-validated; and validation is optional and user-triggered. The exact historical model response and task record for the example were not available, so the specific seven-plan/six-file result cannot be attributed to one LLM response with certainty.

## Architecture diagram

```text
React IDE (AIChat)
    │ create / poll / approve / validate / continue / cancel
    ▼
Frontend AI service ── HTTP ──► FastAPI /ai/agent/tasks
                                  │
                                  ├─ Task store (in-memory cache + SQLAlchemy JSON record)
                                  ├─ AgentTools
                                  │    ├─ project file list / search / inspect
                                  │    └─ lexical relevance + vector RAG context
                                  ├─ TaskPlanner (prompt + JSON/CodeAction parsing)
                                  └─ shared AIService ──► configured Groq or Gemini provider
                                           │
                   proposed file actions ◄─┴─► proposed validation command
                            │                         │
                   explicit user approval      explicit user approval
                            │                         │
                   CodeActionService          ProjectWorkspaceService
                   DB file + index update      DB-backed project snapshot
                            │                         │
                   workspace file sync         Docker sandbox (restricted)
                                                      │ successful changes
                                                      ▼
                                              DB project files / task result
```

`Backend/App/service/AI/agent/agent_orchestrator.py` exists in the current worktree as an **empty, untracked file**. It is not imported by the agent router and is not part of the execution path described above.

## Actual request and execution flow

1. `POST /ai/agent/tasks` checks project ownership, filters and bounds supplied editor context, creates a task, persists it, and schedules `_plan_task` as a FastAPI background task. The task response includes a status, a structured plan, proposed actions, per-task history, step history, and optional validation command/result.
2. The planning worker constructs `AgentTools`, searches the project for up to eight matches, inspects up to six selected/related files, obtains repository context, and builds a bounded context from the project file list, inspected files, and retrieval results.
3. `_request_plan_with_tools` asks the shared AI service for JSON. The tool loop supports listing, reading, and proposing create/update/delete operations or an approved sandbox command. Reads/searches can continue in the loop; a proposed mutation or command pauses the task for user approval.
4. Otherwise, `TaskPlanner.parse_planning_result` validates the structured requirements, task graph, file ownership, actions, and optional command. Invalid plans get up to two repair attempts. A plan with no actions or command gets up to two missing-actions retries unless the tool protocol was used.
5. The scheduler activates only tasks whose prerequisites are complete. Actions for ready tasks wait in `awaiting_approval`; dependent actions remain `scheduled`. `/approve` applies selected changes through `CodeActionService`, checks project ownership and patch freshness, updates repository indexes, and synchronizes the workspace. A successful apply marks a plan task `applied`, not `completed`.
6. Users complete applied tasks through `POST /tasks/{task_id}/plan-tasks/complete` with non-empty verification notes. When an approved validation command is part of the plan, `/validate` checks that all implementation work is ready, requires approval and an exact command match, then runs it through `ProjectWorkspaceService` and the Docker sandbox. Successful sandbox changes are synchronized to project records. Docker unavailability returns an error; the agent does not fall back to host execution.
7. A failed validation does not automatically repair the implementation. The user must continue the failed task; `_run_continuation_task` then requests a correction proposal, which again waits for approval. Retry/iteration limits are bounded.
8. Task state is cached in-process and persisted as a JSON state record. Active tasks restored after server restart are changed to `paused` for user review. The React AI Chat polls active task state and renders plan, steps, approvals, validation, errors, and continuation controls.

## Main implementation locations

| Concern | Existing implementation |
|---|---|
| Agent HTTP API and orchestration | [`agent_router.py`](../Backend/App/api/agent_router.py): task creation, planning, tool dispatch, approval, validation, continuation |
| Task state, planner prompts, parser, command allowlist | [`agent_task_service.py`](../Backend/App/service/AI/agent/agent_task_service.py) |
| Repository-scoped agent tools and RAG | [`agent_tools.py`](../Backend/App/service/AI/agent/agent_tools.py) |
| Typed task and action API schemas | [`agent_schema.py`](../Backend/App/schema/agent_schema.py), [`ai_schema.py`](../Backend/App/schema/ai_schema.py) |
| Groq provider and JSON mode | [`groq_provider.py`](../Backend/App/service/AI/providers/groq_provider.py), [`ai_service.py`](../Backend/App/service/AI/ai_service.py), [`provider_factory.py`](../Backend/App/service/AI/providers/provider_factory.py) |
| Persistent task record | [`agent_task_record.py`](../Backend/App/models/agent_task_record.py); model is included in [`models/__init__.py`](../Backend/App/models/__init__.py) |
| Patch validation and repository reindexing | [`code_action_service.py`](../Backend/App/service/AI/code_action_service.py) |
| Project-scoped workspace and sandbox synchronization | [`project_workspace_service.py`](../Backend/App/service/project_workspace_service.py) |
| Docker execution and resource restrictions | [`sandbox_service.py`](../Backend/App/service/sandbox_service.py) |
| Frontend task contract and API calls | [`aiService.ts`](../Frontend/src/services/aiService.ts); task UI/polling is in [`AIChat.tsx`](../Frontend/src/components/ai/AIChat.tsx) |
| Agent regression coverage | [`test_agent_workflow.py`](../Backend/test_agent_workflow.py) |

## Current planning and execution capabilities

### Planning

- The legacy `plan: list[str]` remains for compatibility, alongside a structured plan containing objective, assumptions, requirements, acceptance criteria, risks, validation strategy, and typed tasks.
- Actions are typed `CodeAction` values. Replace actions require a line range and `old_code`; create-file actions are normalized and validated. Duplicate action paths and unsupported plan operations are rejected.
- Parser limits exist for plan length, action count, output/context size, and tool calls.
- Structured tasks have IDs, dependency edges, expected files, acceptance criteria, verification steps, and lifecycle status. The parser validates dependencies, requirement mappings, acyclicity, and action-to-file coverage.
- Empty action objects are not silently filtered: they fail `CodeAction` validation and cause the entire plan to be repaired. The repair prompt asks the model to regenerate the complete response, including the invalid response; it does not remove or repair the bad item deterministically. Repeated malformed output can exhaust the repair attempts and fail planning.
- Empty `actions: []` is treated differently from an empty action object. Ordinary planning retries missing actions, but a tool-protocol run can finish with no additional changes after a read-only tool sequence.
- Invalid validation commands are caught in `TaskPlanner.parse` and converted to `None`. That can discard a proposed verification command without surfacing a parsing error, undermining the otherwise explicit validation flow.

### Execution and completion

- The system performs a bounded model/tool loop, but it does not schedule independent plan tasks by dependencies or execute an end-to-end implementation autonomously.
- Mutations and commands require approval. Applying approved file changes marks implementation tasks `applied`; it does not complete them.
- A validation command may be absent. Structured tasks then require explicit user verification notes; these are user attestations, not an automated acceptance-criteria evaluator.
- When a validation command exists, it requires separate approval. Validation failure stops for user-directed continuation rather than triggering an automatic bounded repair cycle.
- Failed or rejected actions block dependent tasks. Independent ready tasks can still be approved and applied.
- The legacy tool-protocol path still exists and is not fully governed by the structured-plan lifecycle.
- There is no task pause/resume state machine beyond restart recovery to `paused` and explicit continuation. Worker ownership is maintained in process memory, not by a distributed job/lease mechanism.

## Employee Management regression scenario

The current untracked example is under `Backend/workspaces/1/`: it contains a README and a nested `employee_management` package with `employee.py`, `manager.py`, `database.py`, `main.py`, and `__init__.py`.

Observed issues and likely causes:

1. **Basic model and in-memory storage (baseline observation):** the original task contract did not extract CRUD, persistence, validation, search, or testing requirements. The prompt asked for the “smallest complete set of changes,” and the user did not specify a stack or persistence technology. A simple model and dictionary manager could therefore satisfy the model’s narrow interpretation. `database.py` is also an independent in-memory dictionary, not a persistent database and not used by `EmployeeManager`.
2. **README command mismatch:** the README at `Backend/workspaces/1/README.md` says `python main.py`, but the generated entry point is `employee_management/main.py`; there is no `main.py` at the README’s directory level. The package imports also assume invocation with the package parent on the import path. The current planner validates command syntax/allowlisting, not whether the command matches the generated project layout. This audit did not execute the command.
3. **Malformed `{}` action and repair:** a dict without the required `CodeAction` fields is rejected. The repair loop can ask Groq to regenerate the full plan twice, but there is no deterministic malformed-item repair or task-level partial recovery. A repeated malformed response fails the task rather than being safely removed.
4. **Seven plan steps versus six files:** plan strings and actions are independent arrays. The parser enforces maximum counts and unique changed-file paths but has no dependency or plan-to-file coverage checks. Therefore the mismatch is representable and accepted.
5. **No meaningful verification shown (baseline observation):** validation was optional and separately approved in the baseline workflow. The observed workspace files alone do not prove that the example was run or tested.

The exact historical plan, LLM responses, approval events, and validation result for this workspace example were not recovered in this audit. Findings above distinguish observed artifacts from behavior permitted by the current code.

## Existing capabilities to preserve

- **Groq remains the default LLM provider; Gemini is selectable.** Set `LLM_PROVIDER=gemini`, `GEMINI_API_KEY`, and optionally `GEMINI_MODEL` in the local environment. The default Gemini model is `gemini-3.5-flash-lite`. Gemini uses Google's OpenAI-compatible endpoint, so no additional Python package is required.
- **Quota failover uses the other configured chat provider.** If the active Groq or Gemini provider returns HTTP 402 or 429, chat, pre-output streaming, and each agent planning/execution request are retried once through the other provider when its API key is configured. Other errors do not trigger failover. A stream that already emitted text is not restarted, to avoid duplicate or misleading output. The active provider/model is recorded on successful chat usage and response metadata.
- `LLM_API_KEY` is a legacy Hugging Face key for embeddings; it is not used as a chat-provider fallback. Failover requires both `GROQ_API_KEY` and `GEMINI_API_KEY`.
- **Provider secrets stay local.** Use an ignored local `.env` or environment variables; never commit API keys or paste them into source files, docs, or chat.
- **JSON and function calling are supported by both providers.** Agent tool calls are still validated by the backend and file/command changes still require explicit user approval.
- **RAG and repository search remain active.** Agent context uses repository relevance/search and vector retrieval when available, with a non-vector context fallback.
- **Project/user ownership is checked** at task creation, agent tool construction, task retrieval, file application, and DB-backed workspace execution.
- **Path and sensitive-content checks** constrain file discovery and inspection; patch application checks action/path consistency, source text freshness, and size bounds.
- **File changes and commands require approval.** The current flow supports per-action accept/reject, safe undo, task cancellation, and persisted task history.
- **Execution remains in Docker** with network disabled, CPU/memory/PID limits, dropped Linux capabilities, no-new-privileges, and bounded timeouts. Agent validation refuses host fallback.
- **MCP was not found in the scanned `Backend/App` or `Frontend/src` code, and no MCP tool is registered in `AgentTools`/`_dispatch_agent_tool`.** Any MCP integration elsewhere should be located and mapped before changing the registry; this audit does not establish that no MCP code exists outside the scanned paths.

## Recommended implementation sequence

1. **Phase 1 — Task understanding and planning:** completed; structured schemas, clarification handling, dependency validation, and parse diagnostics are implemented.
2. **Phase 2 — Repository intelligence:** completed; bounded file-derived project profile and combined lexical/vector retrieval are implemented. MCP mapping found no integration to register.
3. **Phase 3 — Execution engine:** completed; dependency-aware task scheduling, explicit completion, persisted per-task history, and correction-plan scheduling are implemented.
4. **Phase 4 — Recovery/replanning:** completed; bounded correction retries and targeted correction planning preserve completed work and require approval for mutations.
5. **Phase 5 — Verification gates:** completed; criterion-level outcomes, explicit `not verified` status, and Docker execution reporting distinguish command success from acceptance.
6. **Phase 6 — Frontend:** completed; AI Chat now presents structured plans, clarification requests, criterion-level verification, and task history.
7. **Phase 7 — Benchmark:** completed; baseline and upgraded agent workflows were compared with repeatable tests and logged outcomes. No live-model or real-Docker equivalence is claimed.

## Remaining acceptance-test gaps

- The Employee Management sample remains incomplete: storage is in-memory, update/delete and input validation are absent, and no project tests are present. Its README command mismatch was confirmed by execution.
- Real Docker integration cannot run on the current laptop; the sandbox image must also be built after Docker installation.
- Unit tests cover interrupted task recovery, dependency scheduling, approved mutations, and task history restoration; a real container run is still pending.

## Phase 1 — Task understanding and planning

Phase 1 adds a typed planning contract while retaining the legacy `plan: list[str]` response for existing consumers:

- [`agent_schema.py`](../Backend/App/schema/agent_schema.py) defines requirement, task, plan, planning-result, and clarification schemas. Validation checks unique IDs, dependency references and cycles, requirement-to-task mappings, pending initial statuses, and required acceptance/verification steps.
- [`agent_task_service.py`](../Backend/App/service/AI/agent/agent_task_service.py) prompts for project scope, requirements, assumptions, dependencies, expected files, acceptance criteria, verification steps, actions, and a validation command. It strictly parses the structured result, checks action/file coverage, and reports malformed or disallowed commands instead of silently dropping them.
- [`agent_router.py`](../Backend/App/api/agent_router.py) persists `plan_details`, adds the `awaiting_clarification` state and `POST /tasks/{task_id}/clarify`, and includes available project metadata in planning context.
- [`groq_provider.py`](../Backend/App/service/AI/providers/groq_provider.py) implements the Groq-compatible chat and agent tool-call flow; [`gemini_provider.py`](../Backend/App/service/AI/providers/gemini_provider.py) reuses it through Google's OpenAI-compatible endpoint.
- [`aiService.ts`](../Frontend/src/services/aiService.ts) adds the API types and clarification request helper. The clarification form and rendering are not implemented yet; they are deferred to Phase 6.
- [`test_agent_workflow.py`](../Backend/test_agent_workflow.py) covers structured parsing, invalid actions/commands, duplicate IDs, dependency errors and cycles, missing verification, clarification, and persisted plan state.

The existing approval and sandbox execution paths remain in place. The legacy tool protocol can still propose a mutation before a structured plan is returned; the structured execution lifecycle is implemented in Phase 3.

## Phase 2 — Repository intelligence

Phase 2 enriches the context sent to planning and correction requests without changing the existing tool registry:

- [`agent_tools.py`](../Backend/App/service/AI/agent/agent_tools.py) now builds a bounded JSON profile from project metadata and safe file records. It reports extension-derived language counts, common manifest and dependency hints, framework signals, entry-point and test-file candidates, configuration/convention files, top-level layout, and simple commands found in README files. Manifest inspection is read-only and uses safe-path/sensitive-content checks.
- [`agent_router.py`](../Backend/App/api/agent_router.py) includes the profile in both initial planning and validation-correction context. `AgentTools` caches the bounded file-path list for the current request so profile construction does not repeat the project-tree lookup.
- [`agent_tools.py`](../Backend/App/service/AI/agent/agent_tools.py) preserves vector retrieval and supplements it with lexical results from files not already represented by vector chunks, deduplicating by path and honoring the existing context-size and safety limits.
- [`agent_task_service.py`](../Backend/App/service/AI/agent/agent_task_service.py) instructs the planner to prefer file-derived evidence, distinguish configured metadata from source signals, and avoid claiming that detected commands or entry points were run.
- MCP mapping found no MCP implementation or declared MCP dependency in the repository code or the backend/frontend dependency manifests searched. The existing agent tool surface remains the local `AgentTools`/router dispatcher; no MCP registry or tool was added.

The profile is explicitly heuristic: entry points, test files, and conventions are candidates based on names/configuration, README commands are not executed, and the manifest parser covers common formats rather than every build ecosystem. At the time of Phase 2, automated acceptance-criteria evaluation and fuller verification reporting remained later-phase work; those verification gates are recorded in Phase 5 below.

## Validation and phase boundary

- Ran `python -m pytest Backend\test_agent_workflow.py -q`: **58 passed, 1 skipped** in 9.58s.
- The skip is the real-Docker integration test when the sandbox image is unavailable; this run therefore does not demonstrate a real container execution.
- Pytest emitted 39 warnings, including a Starlette/httpx deprecation and SQLAlchemy `datetime.utcnow()` deprecations.
- No application source files were created, modified, or deleted for Phase 0. This report is the only file created by this task.
- The worktree already contained unrelated tracked modifications and untracked files before the audit, including the empty `agent_orchestrator.py` and Employee example. They were left untouched.

### Phase 1 validation and status

- Ran `python -m pytest Backend -q`: **306 passed, 3 skipped**. Warnings include existing Starlette/httpx and Pydantic/SQLAlchemy deprecations.
- `npm.cmd run build` in `Frontend` succeeded; Vite reported the existing large-chunk warning.
- `git diff --check` succeeded.
- The tool-based create/update/delete/command approval paths are still supported as they were before Phase 1; therefore the known planning-before-mutation limitation above remains.

### Phase 2 validation and status

- Ran `python -m pytest Backend -q`: **308 passed, 3 skipped**. Existing Starlette/httpx, Pydantic, and SQLAlchemy deprecation warnings remain.
- Added workflow tests for manifest-derived dependencies, entry points, test/configuration discovery, README commands, bounded profile output, and combined vector/lexical retrieval.
- `git diff --check` succeeded.
- No frontend files or MCP tool registrations changed in Phase 2.

### Phase 3 — Dependency-aware task execution

Phase 3 adds dependency-gated execution on top of the structured plans:

- [`agent_router.py`](../Backend/App/api/agent_router.py) associates actions with plan tasks, starts only dependency-ready tasks, keeps future actions `scheduled`, and advances dependents only after prerequisites are explicitly completed. Rejected/failed actions block descendants; independent ready work remains available.
- Applying a file action records the task as `applied`, not `completed`. `POST /tasks/{task_id}/plan-tasks/complete` requires non-empty verification notes. Phase 5 further requires criterion-level outcomes for completion; a successful approved sandbox command alone completes only eligible verification tasks.
- [`agent_task_service.py`](../Backend/App/service/AI/agent/agent_task_service.py) stores lifecycle transitions and verification notes in the existing JSON task record. Restored active task state and per-task history require no database migration.
- Correction proposals use the same action/task association and scheduler while retaining prior applied actions and earlier iteration history.
- [`agent_schema.py`](../Backend/App/schema/agent_schema.py) and [`aiService.ts`](../Frontend/src/services/aiService.ts) expose the lifecycle status, completion request, and task-history response contract. The AI Chat UI has not yet been updated; that remains Phase 6.
- [`test_agent_workflow.py`](../Backend/test_agent_workflow.py) covers dependency staging, explicit verification, rejected-action blocking, persistence/restoration, correction history, and successful validation completion.

Verification notes are explicit user attestations rather than machine evaluation of acceptance criteria. The legacy tool-protocol mutation path remains outside the structured dependency scheduler.

### Phase 3 validation and status

- Ran `python -m pytest Backend -q`: **311 passed, 3 skipped**. The real-Docker integration test is among the skipped cases when the sandbox image is unavailable; this run does not demonstrate a real container execution.
- `npm.cmd run build` in `Frontend` succeeded; TypeScript checking and the Vite production build passed. Vite reported the existing large-chunk warning.
- `git diff --check` succeeded.
- Phases 0–3 are complete; Phase 4 recovery/replanning is recorded below.

### Phase 4 — Recovery and replanning

Phase 4 hardens user-directed recovery after failed validation or partial change application:

- [`config.py`](../Backend/App/config.py) adds `AI_AGENT_MAX_CORRECTION_RETRIES` (default `2`, configurable from `0` to `5`), documented in [`.env.example`](../Backend/.env.example). Invalid correction responses receive only the configured number of targeted repair requests.
- [`agent_task_service.py`](../Backend/App/service/AI/agent/agent_task_service.py) adds a correction-repair prompt that includes the validation failure, parser feedback, and completed-work context. It directs the model to address the failure with minimal changes and not claim execution or apply changes.
- [`agent_router.py`](../Backend/App/api/agent_router.py) includes completed task history and already-applied file paths in correction context. A retry after partial application retains successful action records and lifecycle history; clarification during recovery also retains the prior plan, applied actions, and verification notes.
- Every correction remains a proposal. File changes still wait for explicit approval, and validation commands still require separate approval. Validation is not automatically re-run; the existing overall continuation iteration limit remains in force.
- [`test_agent_workflow.py`](../Backend/test_agent_workflow.py) covers the configurable correction retry limit, a later successful correction after retry exhaustion, preservation of earlier applied changes during planning recovery, and progress preservation across clarification.

### Phase 4 validation and status

- Ran `python -m pytest Backend -q`: **313 passed, 3 skipped**. The real-Docker integration test is among the skipped cases when the sandbox image is unavailable.
- `git diff --check` succeeded.
- No frontend contract or UI changed in Phase 4; the earlier Phase 3 frontend build remains valid for its API type changes.
- Phases 0–4 are complete; Phase 5 is recorded below.

### Phase 5 — Verification gates and acceptance outcomes

Phase 5 distinguishes whether a command ran, whether its checks passed, and whether the plan task's acceptance criteria were actually satisfied:

- [`agent_schema.py`](../Backend/App/schema/agent_schema.py) adds criterion-level `met`, `not_met`, and `not_verified` outcomes. Assessed outcomes require evidence, and completion requires exactly one outcome matching each declared criterion.
- [`agent_router.py`](../Backend/App/api/agent_router.py) no longer treats a passing project command as proof that implementation criteria were met. It records those criteria as `not_verified`; the user can then attest to outcomes and evidence through the plan-task completion endpoint. Missing outcomes preserve an incomplete/not-verified state, unmet criteria fail the task with the evidence retained, and all-met outcomes complete it.
- Successful approved project validation can verify a verification-type task, but does not automatically verify implementation acceptance criteria. An unavailable Docker sandbox returns `executed: false` and `not_verified`, not a command failure; manual criterion assessment remains available.
- [`ai_schema.py`](../Backend/App/schema/ai_schema.py), [`terminalService.ts`](../Frontend/src/services/terminalService.ts), and [`aiService.ts`](../Frontend/src/services/aiService.ts) expose the command execution and verification outcome fields. There is no new UI in this phase.
- [`agent_task_service.py`](../Backend/App/service/AI/agent/agent_task_service.py) persists the top-level verification status and rejects model-supplied verification claims in initial plans.
- [`test_agent_workflow.py`](../Backend/test_agent_workflow.py) covers exact criterion matching, missing or unmet outcomes, passing commands that leave implementation criteria unverified, unavailable Docker, and persisted outcomes.

These are explicit user assessments, not automated semantic evaluation of acceptance criteria. Actual command execution remains restricted to the approved Docker sandbox.

### Phase 5 validation and status

- Ran `python -m pytest Backend -q`: **316 passed, 3 skipped**. Real-Docker integration coverage remains skipped when the sandbox image is unavailable.
- `npm.cmd run build` in `Frontend` succeeded, including TypeScript checking and the Vite production build; Vite reported its existing large-chunk warning.
- `git diff --check` succeeded.
- Phases 0–5 are complete; Phase 6 is recorded below.

### Phase 6 — AI Chat plan and verification experience

Phase 6 surfaces the structured agent lifecycle in the existing AI Chat panel without changing backend behavior:

- [`AIChat.tsx`](../Frontend/src/components/ai/AIChat.tsx) displays the structured plan, requirements, dependencies, expected files, acceptance criteria, verification steps, overall verification status, and task history.
- Clarification-needed tasks show all planner questions with a single answer form and submit through the existing clarification endpoint.
- Active plan tasks expose criterion-by-criterion `met`, `not_met`, or `not_verified` controls, evidence fields, and verification notes. The form only appears when the task is eligible for verification; it submits through the existing plan-task completion API.
- Validation results distinguish a command that passed or failed from Docker-unavailable/not-executed results and from acceptance-criteria failures.
- [`AIChat.css`](../Frontend/src/components/ai/AIChat.css) styles the new plan, clarification, verification, and history views. [`aiService.ts`](../Frontend/src/services/aiService.ts) types the acceptance-criteria failure result returned by the existing API.

### Phase 6 validation and status

- `npm.cmd run build` in `Frontend` succeeded, including TypeScript checking and the Vite production build; Vite reported its large-chunk warning.
- `npm.cmd run lint` succeeded with existing React hook and fast-refresh warnings.
- Ran `python -m pytest Backend -q`: **316 passed, 3 skipped**; Phase 6 does not change backend behavior.
- `git diff --check` succeeded.
- Phases 0–6 are complete; Phase 7 is recorded below.

### Phase 7 — Reproducible baseline comparison

The benchmark uses deterministic workflow tests with fake AI responses; it measures implemented workflow behavior, not model quality or latency.

- **Baseline:** archived Git revision `733c0f5a51fb03575fbf049612e8ec77c5d2b6d7`, which matches the Phase 0 snapshot. **Upgraded:** the current working tree after Phases 1–6.
- The same five existing test names were selected on both snapshots: `test_agent_task_requires_approval_before_applying_changes`, `test_agent_validation_cannot_run_before_change_decision`, `test_agent_validation_does_not_fall_back_to_host_without_docker`, `test_agent_failure_recovery_requires_approval_and_completes`, and `test_agent_create_file_tool_requires_approval_and_persists_afterward`.
- The baseline selection passed **5 tests**; the upgraded selection passed **5 tests**. This confirms those approval, validation, sandbox, and recovery gates continue to pass. The test implementations are taken from each snapshot, so this is a compatibility smoke comparison, not a byte-identical test of changed implementations.
- The full agent workflow suite passed **58 tests with 1 skipped** on the baseline and **72 tests with 1 skipped** on the upgraded tree. The upgraded suite contains 14 more passing workflow cases; suite size alone is not treated as a quality or performance score.
- Six upgraded-only regression scenarios passed: clarification requests, successful validation of verification tasks, passing commands that leave implementation criteria unverified, unmet criterion handling, Docker-unavailable reporting, and correction clarification with applied-work preservation. The baseline snapshot does not contain criterion-result or verification-status fields.
- The existing Employee Management example was exercised directly: its documented `python main.py` command failed because that file is absent in `Backend/workspaces/1`; `python -m employee_management.main` succeeded and printed two employees; `python -m compileall -q employee_management` passed. This validates the existing workspace artifact, not generation by the upgraded agent.

To reproduce the shared comparison from the repository root, extract the baseline revision and use the same test-only environment in both directories:

```powershell
$baseline = Join-Path $env:TEMP 'agent-phase7-baseline'
$archive = Join-Path $env:TEMP 'agent-phase7-baseline.tar'
New-Item -ItemType Directory -Path $baseline
git archive --format=tar --output=$archive 733c0f5a51fb03575fbf049612e8ec77c5d2b6d7
tar -xf $archive -C $baseline
$env:ACCESS_TOKEN_EXPIRE_MINUTES = '30'
$env:SECRET_KEY = 'phase7-benchmark-test-only'
$env:ALGORITHM = 'HS256'
$env:DATABASE_URL = 'sqlite://'
Set-Location $baseline
```

Run this selector from the repository root (the directory containing `Backend`) in both the baseline and upgraded snapshots:

```powershell
python -m pytest Backend\test_agent_workflow.py -q -k "agent_task_requires_approval_before_applying_changes or agent_validation_cannot_run_before_change_decision or agent_validation_does_not_fall_back_to_host_without_docker or agent_failure_recovery_requires_approval_and_completes or agent_create_file_tool_requires_approval_and_persists_afterward"
```

The six upgraded-only regression scenarios can be rerun in the upgraded tree with:

```powershell
python -m pytest Backend\test_agent_workflow.py -q -k "agent_requests_material_clarification_before_proposing_changes or successful_validation_completes_verification_task or passing_command_does_not_claim_implementation_criteria_met or unmet_acceptance_criterion_fails_task_with_evidence or unavailable_docker_reports_verification_as_not_verified or agent_correction_clarification_preserves_applied_work"
```

The full workflow-suite results are reproducible with `python -m pytest Backend\test_agent_workflow.py -q`. Pytest emitted existing Starlette/httpx and SQLAlchemy datetime deprecation warnings. Real-Docker tests were skipped because the sandbox image was unavailable. No live LLM, newly generated project, or real container execution was benchmarked, so no claims are made about model quality, end-to-end generation equivalence, or Docker execution.

### Phase 7 validation and status

- Baseline workflow suite: **58 passed, 1 skipped**.
- Upgraded workflow suite: **72 passed, 1 skipped**.
- Matched baseline/upgraded selection: **5 passed on each**.
- Upgraded-only regression selection: **6 passed**.
- Employee README command: **failed as documented above**; package entry point and compilation passed.
- Full upgraded backend suite: **316 passed, 3 skipped**.
- `git diff --check` passed.

### Current laptop Docker readiness check

The `docker` command is not installed on this laptop. This explains the two Docker-dependent test skips; the third skip in the targeted run is the symlink test, which Windows skipped because the current account lacks symlink privileges.

- Ran `python -m pytest Backend\test_sandbox_service.py Backend\test_project_workspace_service.py Backend\test_agent_workflow.py -q -rs`: **82 passed, 3 skipped**. The non-Docker tests verify the missing-Docker result and that validation does not fall back to executing commands on the host.
- `SandboxService.is_available()` checks for both a working Docker CLI/daemon and the `ai-coding-ide-sandbox:latest` image. Installing Docker alone is not enough; the project image must be built. Docker-unavailable responses remain `executed: false`.
- The existing [sandbox Dockerfile](../Backend/sandbox/Dockerfile) installs Python 3.12, Bash, Node.js/npm, Git, and build tools, and runs as an unprivileged user. The container is launched with networking disabled and resource/capability restrictions.

After installing Docker Desktop and starting its engine, run these commands from the repository root. The image build is required before the integration tests can execute rather than skip:

```powershell
docker version
docker build --tag ai-coding-ide-sandbox:latest --file Backend\sandbox\Dockerfile Backend\sandbox
docker image inspect ai-coding-ide-sandbox:latest
python -m pytest Backend\test_sandbox_service.py Backend\test_project_workspace_service.py Backend\test_agent_workflow.py -q -rs
python -m pytest Backend -q
```

Keep `ENABLE_SANDBOX` unset or set to `true` for the real-container checks. Docker could not be installed or its image built in this session, so actual post-install container execution remains unverified.

Phases 0–7 are complete. The Docker-dependent integration checks are ready to run after installation; no further phase was started.
