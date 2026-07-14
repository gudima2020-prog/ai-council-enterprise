from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    model: str | None = None
    mode: str = "universal"


class ChatResponse(BaseModel):
    answer: str
    model: str
    mode: str


class SettingsResponse(BaseModel):
    default_model: str
    has_openrouter_key: bool


class ModelListResponse(BaseModel):
    models: list[str]
