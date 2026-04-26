"""schemas.py — Pydantic models for structured agent messages."""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class TextPart(BaseModel):
    """A plain-text content part."""
    type: str = "text"
    text: str


class DataPart(BaseModel):
    """A structured data content part (JSON blob)."""
    type: str = "data"
    data: dict = Field(default_factory=dict)


class FilePart(BaseModel):
    """A file reference content part."""
    type: str = "file"
    filename: str
    mime_type: str = "application/octet-stream"
    url: Optional[str] = None
    content: Optional[str] = None


class HandoffRequest(BaseModel):
    """A request to hand off a task from one agent to another."""
    from_agent: Optional[str] = None  # Auto-inferred from task.claimed_by or auth header
    to_agent: str
    message: str = ""
    task_id: Optional[int] = None


class HandoffResponse(BaseModel):
    """A response accepting or rejecting a handoff request."""
    request_id: int
    decision: str = Field(..., pattern="^(accepted|rejected)$")
    reason: Optional[str] = None


class AgentMessage(BaseModel):
    """A generic agent-to-agent message with typed content parts."""
    id: str = ""
    task_id: Optional[int] = None
    from_agent: str
    to_agent: str
    parts: list = Field(default_factory=list)  # list of TextPart | DataPart | FilePart
    timestamp: Optional[str] = None
    metadata: dict = Field(default_factory=dict)
