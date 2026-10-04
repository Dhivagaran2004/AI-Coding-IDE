import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import func
from sqlalchemy.orm import Session

from App.api.terminal_router import terminal_service
from App.auth.auth import get_current_user
from App.config import (
    AI_CONTEXT_MAX_CHARS,
    AI_DAILY_REQUEST_LIMIT,
    AI_DAILY_TOKEN_LIMIT,
    AI_OUTPUT_TOKEN_RESERVATION,
    LLM_MODEL,
    LLM_PROVIDER,
)
from App.database.database import get_db
from App.models.ai_conversation import AIConversation, AIConversationMessage
from App.models.project import Project
from App.models.project_file import ProjectFile
from App.models.repository_index import RepositoryIndex
from App.models.user import User
from App.schema.ai_schema import (
    CodeAction,
    AIChatRequest,
    AIChatResponse,
    TerminalContext,
)
from App.service.AI.ai_service import AIService
from App.service.AI.context.repository_relevance import (
    RepositoryRelevanceService,
)
from App.service.AI.context.context_budget import (
    ContextBudgetExceeded,
    SYSTEM_PROMPT_RESERVE,
    build_budgeted_context,
)
from App.service.AI.context.context_cache import project_structure_cache
from App.service.AI.context.repository_context import (
    RepositoryContextBuilder,
)
from App.service.AI.index.indexed_context_service import (
    IndexedContextService,
)
from App.service.AI.index.vector_rag_service import VectorRAGService
from App.service.AI.providers.provider_factory import (
    get_llm_provider,
)
from App.service.AI.usage.usage_service import (
    AIUsageService,
    UsageLimitExceeded,
)
from App.service.git_service import GitCommandError, GitService


router = APIRouter(
    prefix="/ai",
    tags=["AI"],
)

logger = logging.getLogger(__name__)


def _project_structure_context(
    db: Session,
    user_id: int,
    project_id: int,
) -> str:
    if not isinstance(db, Session):
        return RepositoryContextBuilder(
            db=db,
            project_id=project_id,
            max_chars=12000,
        ).build_tree()

    file_metadata = (
        db.query(
            func.count(ProjectFile.id),
            func.max(ProjectFile.updated_at),
            func.coalesce(func.sum(ProjectFile.id), 0),
        )
        .filter(ProjectFile.project_id == project_id)
        .one()
    )
    index_metadata = (
        db.query(
            func.count(RepositoryIndex.id),
            func.max(RepositoryIndex.updated_at),
        )
        .filter(RepositoryIndex.project_id == project_id)
        .one()
    )
    cache_key = (
        user_id,
        project_id,
        file_metadata[0],
        file_metadata[1],
        file_metadata[2],
        index_metadata[0],
        index_metadata[1],
    )
    return project_structure_cache.get_or_create(
        cache_key,
        lambda: RepositoryContextBuilder(
            db=db,
            project_id=project_id,
            max_chars=12000,
        ).build_tree(),
    )


def _extract_code_action(message: str) -> CodeAction | None:
    for match in re.finditer(
        r"```json\s*([\s\S]*?)```",
        message,
        flags=re.IGNORECASE,
    ):
        try:
            return CodeAction.model_validate(json.loads(match.group(1)))
        except (ValueError, TypeError):
            continue
    return None


def _extract_plan(message: str) -> list[str] | None:
    candidates = [message]
    candidates.extend(
        match.group(1)
        for match in re.finditer(
            r"```(?:json)?\s*([\s\S]*?)```",
            message,
            flags=re.IGNORECASE,
        )
    )
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(payload, dict) and isinstance(payload.get("plan"), list):
            steps = payload["plan"]
            if len(steps) <= 12 and all(isinstance(step, str) for step in steps):
                return [step[:500] for step in steps]
    return None


def _requests_git_review(message: str) -> bool:
    return bool(
        re.search(
            r"\b(?:review|summarize|explain)\b.{0,100}\b(?:changes|diff)\b",
            message,
            flags=re.IGNORECASE | re.DOTALL,
        )
    )


def _persist_user_message(
    db: Session,
    request: AIChatRequest,
    conversation: AIConversation,
) -> None:
    db.add(
        AIConversationMessage(
            conversation_id=conversation.id,
            role="user",
            content=request.message,
            message_metadata={"mode": request.mode},
        )
    )

    conversation.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()


def _persist_assistant_message(
    db: Session,
    response: AIChatResponse,
    conversation: AIConversation,
) -> int:
    db.add(
        AIConversationMessage(
            conversation_id=conversation.id,
            role="assistant",
            content=response.message,
            message_metadata={
                "provider": response.provider,
                "model": response.model,
            },
        )
    )
    conversation.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
    response.conversation_id = conversation.id
    return conversation.id


# =========================================================
# AI SERVICE
# =========================================================

try:
    ai_service = AIService(
        provider=get_llm_provider(LLM_PROVIDER)
    )
except Exception as exc:
    ai_service = None
    logger.warning("AI provider initialization failed (%s).", type(exc).__name__)


# =========================================================
# AI CHAT
# =========================================================

async def _chat(
    request: AIChatRequest,
    db: Session,
    current_user: User,
    streaming: bool = False,
):
    """
    Send a message to the AI coding assistant.

    If project_id is supplied, the backend searches
    the repository and adds relevant files to the
    AI context.
    """

    if ai_service is None:
        raise HTTPException(
            status_code=503,
            detail="AI provider is not configured.",
        )

    usage_service = (
        AIUsageService(
            db=db,
            request_limit=AI_DAILY_REQUEST_LIMIT,
            token_limit=AI_DAILY_TOKEN_LIMIT,
            output_reservation=AI_OUTPUT_TOKEN_RESERVATION,
        )
        if isinstance(db, Session)
        else None
    )
    usage_id = None
    input_token_count = 0
    usage_completed = False

    try:

        # =====================================================
        # CONVERSATION HISTORY
        # =====================================================

        history = [
            {
                "role": item.role,
                "content": item.content,
            }
            for item in request.history
        ]
        conversation = None
        if request.conversation_id is not None:
            conversation = (
                db.query(AIConversation)
                .filter(
                    AIConversation.id == request.conversation_id,
                    AIConversation.user_id == current_user.id,
                )
                .first()
            )
            if conversation is None:
                raise HTTPException(status_code=404, detail="Conversation not found.")
            if (
                request.project_id is not None
                and conversation.project_id != request.project_id
            ):
                raise HTTPException(status_code=404, detail="Conversation not found.")
            if conversation.project_id is not None:
                owned_project = (
                    db.query(Project.id)
                    .filter(
                        Project.id == conversation.project_id,
                        Project.user_id == current_user.id,
                    )
                    .first()
                )
                if owned_project is None:
                    raise HTTPException(
                        status_code=404,
                        detail="Conversation not found.",
                    )
                project_id = conversation.project_id
            else:
                project_id = None
            stored_messages = (
                db.query(AIConversationMessage)
                .filter(
                    AIConversationMessage.conversation_id == conversation.id,
                    AIConversationMessage.role.in_(("user", "assistant")),
                )
                .order_by(
                    AIConversationMessage.created_at.desc(),
                    AIConversationMessage.id.desc(),
                )
                .limit(50)
                .all()
            )
            history = [
                {"role": item.role, "content": item.content}
                for item in reversed(stored_messages)
            ]
        else:
            project_id = request.project_id

        # =====================================================
        # REPOSITORY CONTEXT
        # =====================================================

        repository_context = None
        terminal_context_block = None
        selected_context_block = None

        if request.terminal_context is not None:
            terminal = request.terminal_context
            terminal_context = (
                "TERMINAL CONTEXT\n"
                f"STDERR:\n{terminal.stderr}\n"
                f"EXIT CODE: {terminal.exit_code}\n"
                f"COMMAND: {terminal.command}\n"
                f"SUCCESS: {terminal.success}\n"
                f"STDOUT:\n{terminal.stdout}"
            )
            terminal_context_block = terminal_context

        if request.selected_code is not None:
            selection = request.selected_code
            selected_context_block = (
                "SELECTED CODE CONTEXT\n"
                f"FILE: {selection.file_path}\n"
                f"LANGUAGE: {selection.language}\n"
                f"LINES: {selection.start_line}-{selection.end_line}\n"
                "SELECTED CODE:\n"
                f"```{selection.language}\n{selection.code}\n```"
            )

        if project_id is not None:

            project = (
                db.query(Project)
                .filter(
                    Project.id == project_id,
                    Project.user_id == current_user.id,
                )
                .first()
            )
            if project is None:
                raise HTTPException(
                    status_code=404,
                    detail="Project not found",
                )

            project_structure = _project_structure_context(
                db=db,
                user_id=current_user.id,
                project_id=project_id,
            )

            relevance_service = (
                RepositoryRelevanceService(
                    db=db,
                    project_id=project_id,
                )
            )

            repository_result = (
                relevance_service.build_context(
                    query=request.message,
                )
            )

            repository_context = repository_result.content
            indexed_result = IndexedContextService(
                db=db,
                project_id=project_id,
            ).build_context(
                repository_result.selected_files
            )
            if (
                repository_result.selected_file_count > 0
                and indexed_result.file_count
                == repository_result.selected_file_count
            ):
                repository_context = indexed_result.content

            try:
                vector_result = VectorRAGService(
                    db=db,
                    project_id=project_id,
                ).search_project_context(
                    project_id=project_id,
                    query=request.message,
                    top_k=5,
                )
                if vector_result.content:
                    repository_context = vector_result.content
                elif not vector_result.available:
                    logger.warning(
                        "Vector retrieval unavailable for project %s; "
                        "using existing repository context.",
                        project_id,
                    )
                else:
                    logger.info(
                        "Vector retrieval returned no relevant chunks for project %s; "
                        "using existing repository context.",
                        project_id,
                    )
            except Exception as exc:
                logger.warning(
                    "Vector retrieval failed for project %s; "
                    "using existing repository context (%s).",
                    project_id,
                    type(exc).__name__,
                )

            git_review_context = None
            if _requests_git_review(request.message):
                try:
                    git_service = GitService(
                        terminal_service.validate_project_directory(project_id)
                    )
                    git_status = git_service.status()
                    working_diff = git_service.diff()["diff"]
                    staged_diff = git_service.diff(staged=True)["diff"]
                    safe_diff = TerminalContext(
                        command="git diff",
                        exit_code=0,
                        stdout=(
                            f"WORKING TREE DIFF:\n{working_diff[:12000]}\n"
                            f"STAGED DIFF:\n{staged_diff[:12000]}"
                        ),
                        success=True,
                    ).stdout
                    git_review_context = (
                        "GIT WORKING TREE\n"
                        f"BRANCH: {git_status['branch']}\n"
                        "CHANGED FILES:\n"
                        f"{json.dumps(git_status['changed_files'], ensure_ascii=False)}\n"
                        "DIFF (secrets filtered):\n"
                        f"{safe_diff}"
                    )
                except (GitCommandError, OSError) as exc:
                    logger.info(
                        "Git context unavailable for project %s (%s).",
                        project_id,
                        type(exc).__name__,
                    )

            repository_context = "\n\n".join(
                part
                for part in (
                    project_structure,
                    repository_context,
                    git_review_context,
                )
                if part
            )

        # =====================================================
        # AI REQUEST
        # =====================================================

        mode_instructions = {
            "plan": (
                "Planning mode: analyze the requested task and return only "
                "a JSON object with a 'plan' array of concise, ordered steps. "
                "Do not generate a patch or claim to have modified files."
            ),
            "edit": (
                "Edit mode: propose a minimal structured code change. "
                "Include a valid code_change JSON object in a fenced json "
                "block with exact file_path, operation, line range, old_code, "
                "new_code, and description. The user must review and approve "
                "it before it is applied."
            ),
        }
        message = request.message
        if request.mode in mode_instructions:
            message = (
                f"{mode_instructions[request.mode]}\n\n"
                f"USER TASK:\n{request.message}"
            )

        context_parts = [
            ("selected", selected_context_block),
            ("repository", repository_context),
            ("current_file", request.context),
        ]
        if terminal_context_block:
            context_parts = [
                ("repository", repository_context),
                ("terminal", terminal_context_block),
                ("selected", selected_context_block),
                ("current_file", request.context),
            ]
        try:
            context, history = build_budgeted_context(
                message=message,
                history=history,
                context_parts=context_parts,
                max_prompt_chars=AI_CONTEXT_MAX_CHARS,
            )
        except ContextBudgetExceeded as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc

        if usage_service is not None:
            input_token_count = usage_service.estimate_tokens(
                " " * SYSTEM_PROMPT_RESERVE
                + message
                + (context or "")
                + "".join(item["content"] for item in history)
            )
            try:
                usage_id = usage_service.start_request(
                    user_id=current_user.id,
                    project_id=project_id,
                    conversation_id=request.conversation_id,
                    provider=LLM_PROVIDER,
                    model=LLM_MODEL,
                    estimated_input_tokens=input_token_count,
                )
            except UsageLimitExceeded as exc:
                raise HTTPException(
                    status_code=429,
                    detail=str(exc),
                ) from exc

        if request.conversation_id is not None:
            if conversation is None:
                raise HTTPException(status_code=404, detail="Conversation not found.")
            _persist_user_message(db, request, conversation)

        if streaming:
            async def events() -> AsyncIterator[str]:
                nonlocal usage_completed
                collected: list[str] = []
                try:
                    async for chunk in ai_service.stream(
                        message=message,
                        context=context,
                        history=history,
                    ):
                        collected.append(chunk)
                        yield f"data: {json.dumps({'type': 'token', 'content': chunk})}\n\n"

                    response_text = "".join(collected)
                    if usage_service is not None and usage_id is not None:
                        usage_service.finish_request(
                            usage_id,
                            input_tokens=input_token_count,
                            output_tokens=usage_service.estimate_tokens(
                                response_text
                            ),
                            request_status="succeeded",
                        )
                    usage_completed = True
                    final_response = AIChatResponse(
                        message=response_text,
                        provider=LLM_PROVIDER,
                        model=LLM_MODEL,
                        code_action=(
                            _extract_code_action(response_text)
                            if request.mode != "plan"
                            else None
                        ),
                        plan=(
                            _extract_plan(response_text)
                            if request.mode == "plan"
                            else None
                        ),
                    )
                    if request.conversation_id is not None:
                        _persist_assistant_message(
                            db,
                            final_response,
                            conversation,
                        )
                    yield (
                        "data: "
                        f"{json.dumps({'type': 'done', 'response': final_response.model_dump()})}\n\n"
                    )
                except SQLAlchemyError as exc:
                    db.rollback()
                    if (
                        usage_service is not None
                        and usage_id is not None
                        and not usage_completed
                    ):
                        usage_service.finish_request(
                            usage_id,
                            input_tokens=input_token_count,
                            output_tokens=usage_service.estimate_tokens(
                                "".join(collected)
                            ),
                            request_status="failed",
                            error_status="persistence_error",
                        )
                    logger.warning(
                        "AI conversation persistence failed (%s).",
                        type(exc).__name__,
                    )
                    yield (
                        "data: "
                        f"{json.dumps({'type': 'error', 'detail': 'Unable to save the conversation. Please try again.'})}\n\n"
                    )
                except asyncio.CancelledError:
                    if (
                        usage_service is not None
                        and usage_id is not None
                        and not usage_completed
                    ):
                        usage_service.finish_request(
                            usage_id,
                            input_tokens=input_token_count,
                            output_tokens=usage_service.estimate_tokens(
                                "".join(collected)
                            ),
                            request_status="cancelled",
                            error_status="client_cancelled",
                        )
                    raise
                except Exception as exc:
                    if (
                        usage_service is not None
                        and usage_id is not None
                        and not usage_completed
                    ):
                        usage_service.finish_request(
                            usage_id,
                            input_tokens=input_token_count,
                            output_tokens=usage_service.estimate_tokens(
                                "".join(collected)
                            ),
                            request_status="failed",
                            error_status=_usage_error_status(exc),
                        )
                    logger.warning(
                        "AI streaming request failed (%s).",
                        type(exc).__name__,
                    )
                    yield (
                        "data: "
                        f"{json.dumps({'type': 'error', 'detail': 'AI provider is temporarily unavailable. Try again.'})}\n\n"
                    )

            return StreamingResponse(
                events(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                },
            )

        response = await ai_service.chat(
            message=message,
            context=context,
            history=history,
        )
        if usage_service is not None and usage_id is not None:
            usage_service.finish_request(
                usage_id,
                input_tokens=input_token_count,
                output_tokens=usage_service.estimate_tokens(response),
                request_status="succeeded",
            )
            usage_completed = True

        chat_response = AIChatResponse(
            message=response,
            provider=LLM_PROVIDER,
            model=LLM_MODEL,
            code_action=(
                _extract_code_action(response)
                if request.mode != "plan"
                else None
            ),
            plan=_extract_plan(response) if request.mode == "plan" else None,
        )
        if request.conversation_id is not None:
            _persist_assistant_message(
                db,
                chat_response,
                conversation,
            )
        return chat_response

    except HTTPException:
        raise

    except SQLAlchemyError as exc:
        db.rollback()
        if usage_service is not None and usage_id is not None and not usage_completed:
            usage_service.finish_request(
                usage_id,
                input_tokens=input_token_count,
                output_tokens=0,
                request_status="failed",
                error_status="persistence_error",
            )
        logger.warning("AI conversation persistence failed (%s).", type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail="Unable to save the conversation. Please try again.",
        ) from exc

    except Exception as exc:
        if usage_service is not None and usage_id is not None and not usage_completed:
            usage_service.finish_request(
                usage_id,
                input_tokens=input_token_count,
                output_tokens=0,
                request_status="failed",
                error_status=_usage_error_status(exc),
            )
        logger.warning("AI provider request failed (%s).", type(exc).__name__)
        raise HTTPException(
            status_code=502,
            detail="AI provider is temporarily unavailable. Try again.",
        ) from exc


def _usage_error_status(error: Exception) -> str:
        status_code = getattr(error, "status_code", None)
        if isinstance(error, (TimeoutError,)):
            return "provider_timeout"
        if status_code in (401, 403):
            return "provider_authentication"
        if isinstance(status_code, int) and status_code >= 500:
            return "provider_server_error"
        if isinstance(status_code, int) and status_code == 429:
            return "provider_rate_limited"
        return "provider_error"


@router.post("/chat", response_model=AIChatResponse)
async def chat(
    request: AIChatRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await _chat(request, db, current_user)


@router.post("/chat/stream")
async def chat_stream(
    request: AIChatRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await _chat(request, db, current_user, streaming=True)