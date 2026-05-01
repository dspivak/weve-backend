from __future__ import annotations
from typing import Any
from pydantic import BaseModel


class ConversationMessage(BaseModel):
    role: str  # "user" | "assistant"
    content: str


class RealtimeTaskOutcome(BaseModel):
    id: str
    label: str
    likelihood: int | None = None


class RealtimeTask(BaseModel):
    id: str
    label: str
    outcomes: list[RealtimeTaskOutcome] = []
    plausibility: str | None = None
    parentId: str | None = None
    chosenOutcome: str | None = None


class RealtimeFlowchart(BaseModel):
    goal: str | None = None
    goalSpec: dict[str, Any] | None = None
    goalOutcomes: list[dict[str, Any]] | None = None
    valueCurrency: str | None = None
    tasks: list[RealtimeTask] = []


class ConverseRequest(BaseModel):
    messages: list[ConversationMessage]
    current_state: RealtimeFlowchart | None = None
    chattiness: str = "terse"          # terse | balanced | friendly
    previous_confusion: str | None = None


class ConverseResponse(BaseModel):
    ai_response: str
    operations: list[dict[str, Any]]
    confusion: str | None = None


class EvaluateRequest(BaseModel):
    current_state: RealtimeFlowchart
    conversation_summary: str          # pre-formatted by frontend (formatConversationForExtraction)


class EvaluateResponse(BaseModel):
    operations: list[dict[str, Any]]


class FlowchartSaveRequest(BaseModel):
    flowchart_json: dict[str, Any]     # the TaskNode tree (ct/types.ts: TaskNode)


class FlowchartResponse(BaseModel):
    post_id: str
    flowchart_json: dict[str, Any] | None = None
