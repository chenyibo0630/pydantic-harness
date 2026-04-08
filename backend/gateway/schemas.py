from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=32_000)
    agent: str = Field(default="main")
    conversation_id: str | None = Field(default=None, description="Pass to continue a conversation")


class ChatError(BaseModel):
    error: str
    code: str
    detail: str | None = None
