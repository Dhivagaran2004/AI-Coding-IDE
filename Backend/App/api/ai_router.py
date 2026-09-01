from fastapi import APIRouter

from App.schema.ai_schema import AIChatRequest, AIChatResponse
from App.service.AI.ai_service import AIService


router = APIRouter(
    prefix="/ai",
    tags=["AI"],
)

ai_service = AIService()


@router.post("/chat", response_model=AIChatResponse)
async def chat(request: AIChatRequest):
    """
    Send a message to the AI assistant.
    """

    response = await ai_service.chat(
        message=request.message,
        context=request.context,
    )

    return AIChatResponse(
        message=response,
    )