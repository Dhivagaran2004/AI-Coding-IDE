from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from App.config import LLM_MODEL, LLM_PROVIDER
from App.database.database import get_db
from App.schema.ai_schema import (
    AIChatRequest,
    AIChatResponse,
)
from App.service.AI.ai_service import AIService
from App.service.AI.context.repository_question_service import (
    RepositoryQuestionService,
)
from App.service.AI.providers.provider_factory import (
    get_llm_provider,
)


router = APIRouter(
    prefix="/ai",
    tags=["AI"],
)


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

        project_id = getattr(
            request,
            "project_id",
            None,
        )

        if project_id is not None:

            relevance_service = (
                RepositoryQuestionService(
                    db=db,
                    project_id=project_id,
                )
            )

            repository_result = (
                relevance_service.build_context(
                    question=request.message,
                )
            )

            repository_context = (
                repository_result.content
            )

            if repository_context:

                if context:

                    context = (
                        "CURRENT IDE CONTEXT\n\n"
                        f"{context}\n\n"
                        "REPOSITORY CONTEXT\n\n"
                        f"{repository_context}"
                    )

                else:

                    context = repository_context

        # =====================================================
        # AI REQUEST
        # =====================================================

        response = await ai_service.chat(
            message=request.message,
            context=context,
            history=history,
        )

        return AIChatResponse(
            message=response,
            provider=LLM_PROVIDER,
            model=LLM_MODEL,
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