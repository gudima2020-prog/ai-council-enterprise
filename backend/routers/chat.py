from fastapi import APIRouter, HTTPException

from backend.core.config import get_settings
from backend.core.events import event_bus
from backend.gateway.factory import build_ai_gateway
from backend.gateway.prompts import get_system_prompt
from backend.schemas import ChatRequest, ChatResponse

router = APIRouter(tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    settings = get_settings()
    gateway = build_ai_gateway(
        settings=settings,
        event_bus=event_bus,
    )

    response = await gateway.ask(
        user_prompt=request.message,
        system_prompt=get_system_prompt(request.mode),
        model=request.model,
        mode=request.mode,
        source="chat",
    )

    if response.status != "success":
        detail = (
            response.error.message
            if response.error
            else "Неизвестная ошибка AI Gateway."
        )
        raise HTTPException(status_code=502, detail=detail)

    return ChatResponse(
        answer=response.content,
        model=response.model,
        mode=request.mode,
    )
