from fastapi import APIRouter, HTTPException

from App.config import (
    LLM_MODEL,
    LLM_PROVIDER,
)

from App.schema.ai_schema import (
    AIChatRequest,
    AIChatResponse,
)

from App.service.AI.ai_service import AIService
from App.service.AI.providers.provider_factory import (
    get_llm_provider,
)


router = APIRouter(
    prefix="/ai",
    tags=["AI"],
)


try:
    ai_service = AIService(
        provider=get_llm_provider(LLM_PROVIDER)
    )
except Exception as exc:
    ai_service = None
    provider_error = str(exc)


@router.post(
    "/chat",
    response_model=AIChatResponse,
)
async def chat(request: AIChatRequest):
    """
    Send a message to the AI coding assistant.
    """

    if ai_service is None:
        raise HTTPException(
            status_code=503,
            detail=f"AI provider unavailable: {provider_error}",
        )

    try:
        history = [
            {
                "role": item.role,
                "content": item.content,
            }
            for item in request.history
        ]

        response = await ai_service.chat(
            message=request.message,
            context=request.context,
            history=history,
        )

        return AIChatResponse(
            message=response,
            provider=LLM_PROVIDER,
            model=LLM_MODEL,
        )

    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"AI provider request failed: {str(exc)}",
        ) from exc