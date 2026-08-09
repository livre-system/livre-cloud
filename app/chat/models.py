from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class Message(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[Message]
    provider: str | None = None
    chat_id: str | None = None  # Si viene, agrega al chat existente
    mode: str | None = "conspiranoico"


class SynthesizeRequest(BaseModel):
    text: str
    voice_id: str | None = None


class ChatCreateResponse(BaseModel):
    chat_id: str
    title: str
    created_at: datetime


class ChatSummary(BaseModel):
    chat_id: str
    title: str
    created_at: datetime
    updated_at: datetime


class ChatHistory(BaseModel):
    chat_id: str
    title: str
    messages: list[Message]
