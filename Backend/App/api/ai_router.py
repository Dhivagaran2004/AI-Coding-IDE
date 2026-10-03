import json
import logging
import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from App.auth.auth import get_current_user
from App.config import LLM_MODEL, LLM_PROVIDER
from App.database.database import get_db
from App.models.project import Project
from App.models.user import User
from App.schema.ai_schema import (
    CodeAction,
    AIChatRequest,
    AIChatResponse,
)
from App.service.AI.ai_service import AIService
from App.service.AI.context.repository_relevance import (
    RepositoryRelevanceService,
)
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


router = APIRouter(
    prefix="/ai",
    tags=["AI"],
)

logger = logging.getLogger(__name__)
MAX_AI_CONTEXT_CHARS = 100000


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


# =========================================================
# AI SERVICE
# =========================================================

try:
    ai_service = AIService(
        provider=get_llm_provider(LLM_PROVIDER)
    )
except Exception as exc:
    ai_service = None
    provider_error = str(exc)


# =========================================================
# AI CHAT
# =========================================================

@router.post(
    "/chat",
    response_model=AIChatResponse,
)
async def chat(
    request: AIChatRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
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
            detail=(
                "AI provider unavailable: "
                f"{provider_error}"
            ),
        )

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

        # =====================================================
        # REPOSITORY CONTEXT
        # =====================================================

        context = request.context
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

        project_id = getattr(
            request,
            "project_id",
            None,
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

            project_structure = RepositoryContextBuilder(
                db=db,
                project_id=project_id,
                max_chars=12000,
            ).build_tree()

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

            repository_context = "\n\n".join(
                part
                for part in (
                    project_structure,
                    repository_context,
                )
                if part
            )

            if terminal_context_block or selected_context_block:
                prioritized_context = []
                if terminal_context_block:
                    if repository_context:
                        prioritized_context.append(
                            f"REPOSITORY CONTEXT\n\n{repository_context}"
                        )
                    prioritized_context.append(terminal_context_block)
                    if selected_context_block:
                        prioritized_context.append(selected_context_block)
                else:
                    if selected_context_block:
                        prioritized_context.append(selected_context_block)
                    if repository_context:
                        prioritized_context.append(
                            f"REPOSITORY CONTEXT\n\n{repository_context}"
                        )
                if request.context:
                    prioritized_context.append(
                        f"CURRENT FILE CONTEXT\n\n{request.context}"
                    )
                context = "\n\n".join(prioritized_context) or None
            elif repository_context:
                context = (
                    "CURRENT IDE CONTEXT\n\n"
                    f"{context}\n\n"
                    "REPOSITORY CONTEXT\n\n"
                    f"{repository_context}"
                    if context
                    else repository_context
                )

        # =====================================================
        # AI REQUEST
        # =====================================================

        if context and len(context) > MAX_AI_CONTEXT_CHARS:
            logger.warning(
                "AI context exceeded the request limit and was truncated."
            )
            context = context[:MAX_AI_CONTEXT_CHARS]

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

        response = await ai_service.chat(
            message=message,
            context=context,
            history=history,
        )

        return AIChatResponse(
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

    except HTTPException:
        raise

    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                "AI provider request failed: "
                f"{str(exc)}"
            ),
        ) from exc