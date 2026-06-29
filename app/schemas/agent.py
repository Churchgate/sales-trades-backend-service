from pydantic import BaseModel, model_validator

_VALID_INTENTS = {"note", "task", "note+task"}


class AgentLogRequest(BaseModel):
    """Structured payload from the logging agent (n8n, after LLM extraction)."""

    intent: str  # note | task | note+task
    deal_id: int | None = None
    deal_hint: str | None = None
    note_text: str | None = None
    task_title: str | None = None
    due_date: str | None = None  # YYYY-MM-DD or ISO-8601
    owner_id: int | None = None
    message_id: str | None = None  # source message id (idempotency — reserved)

    @model_validator(mode="after")
    def _check(self) -> "AgentLogRequest":
        if self.intent not in _VALID_INTENTS:
            raise ValueError(f"intent must be one of {sorted(_VALID_INTENTS)}")
        if self.deal_id is None and not self.deal_hint:
            raise ValueError("provide deal_id or deal_hint")
        if self.intent in {"note", "note+task"} and not self.note_text:
            raise ValueError("note_text is required for a note intent")
        if self.intent in {"task", "note+task"} and not self.task_title:
            raise ValueError("task_title is required for a task intent")
        return self


class AgentLogResponse(BaseModel):
    status: str = "ok"
    deal_id: int
    deal_name: str
    note_id: int | None = None
    task_id: int | None = None
    confirmation: str
