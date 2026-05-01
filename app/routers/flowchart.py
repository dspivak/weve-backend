"""
Flowchart endpoints for Plausible Fiction.

POST /api/flowchart/converse   — conversation + extraction LLMs in parallel
POST /api/flowchart/evaluate   — per-task evaluator (likelihoods)
GET  /api/posts/{post_id}/flowchart
PUT  /api/posts/{post_id}/flowchart
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Annotated, Any, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings
from app.flowchart_validator import validate_flowchart_structure
from app.routers.auth import get_current_user_and_token

_DEV_MODE = settings.env.lower() in ("dev", "development", "local")
_security = HTTPBearer(auto_error=False)

def _get_optional_user(
    credentials: Annotated[Optional[HTTPAuthorizationCredentials], Depends(_security)],
) -> Optional[tuple[dict, str]]:
    """In dev mode, auth is optional for converse/evaluate. In prod, require it."""
    if _DEV_MODE and (not credentials or not credentials.credentials):
        return {"id": "dev-user", "email": "dev@local"}, "dev-token"
    if not credentials or not credentials.credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")
    from app.routers.auth import _get_user_from_supabase_token  # local import to avoid circular
    user_data = _get_user_from_supabase_token(credentials.credentials)
    if not user_data:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return user_data, credentials.credentials
from app.schemas.flowchart import (
    ConverseRequest,
    ConverseResponse,
    EvaluateRequest,
    EvaluateResponse,
    FlowchartResponse,
    FlowchartSaveRequest,
)

router = APIRouter(tags=["flowchart"])

_CONVERSATION_MODEL = "claude-sonnet-4-6"
_EXTRACTION_MODEL = "claude-sonnet-4-6"
_EVALUATOR_MODEL = "claude-haiku-4-5-20251001"

_SUPABASE_REST = "/rest/v1"


def _supabase_headers(access_token: str) -> dict:
    return {
        "apikey": settings.supabase_anon_key,
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }


def _rest_url(path: str) -> str:
    return f"{settings.supabase_url.rstrip('/')}{_SUPABASE_REST}{path}"


def _anthropic_client():
    import anthropic
    if not settings.anthropic_api_key:
        raise HTTPException(status_code=503, detail="ANTHROPIC_API_KEY not configured.")
    return anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)


async def _call_anthropic(system: str, messages: list[dict], model: str) -> str:
    client = _anthropic_client()
    response = await client.messages.create(
        model=model,
        max_tokens=2048,
        system=system,
        messages=messages,
    )
    return response.content[0].text if response.content else ""


def _parse_operations(raw: str) -> tuple[list[dict], str | None]:
    """Extract operations list and optional confusion from LLM JSON output."""
    match = re.search(r"\{[\s\S]*\}", raw)
    if not match:
        return [], None
    try:
        parsed = json.loads(match.group())
        return parsed.get("operations", []), parsed.get("confusion")
    except json.JSONDecodeError:
        return [], None


# =============================================================================
# Prompt helpers (ported from src/llm/prompts.js)
# =============================================================================

def _get_conversation_prompt(chattiness: str = "terse") -> str:
    base = """You help people build a "plausible fiction" — a concrete story of how they get from where they are today to a goal they want to achieve.

YOUR ROLE:
You're a collaborator. Your job is to LISTEN first, then help them think through the steps.

LISTENING vs PROBING:
- When the user is telling you their plan, LET THEM TALK. Acknowledge briefly ("Got it" / "OK") and let them continue.
- Only probe when the user seems done — short messages, vague statements, or they ask you a question.
- If the user says "I want to talk about X", go there immediately. Don't ask more questions about Y.
- If the user gives you several details at once, acknowledge them — don't interrogate each one.
- The user knows what they want to discuss. Your questions should fill gaps THEY haven't addressed, not redirect them.

HOW TO QUESTION (when it's time):
- Ask about SUBSTANCE first: what, how, why. "What kind of rocket?" not "What's your budget?"
- Understand the plan before questioning logistics (budget, timeline, resources come later).
- When something seems implausible, note it briefly and move on — don't block the conversation. You can come back to it.
- ONE question at a time. Never stack two questions in one response.

PLAUSIBILITY AND LIKELIHOODS:
- As you discuss each step, assess how likely it is to succeed. Draw this out of the user.
- Ask questions that reveal confidence: "Have you done this before?", "What could go wrong?", "How many people have pulled this off?"
- When the user says something is easy or certain, probe: "What makes you confident?"
- When something seems risky, explore the failure modes: "What happens if that doesn't work?"

WHAT YOU'RE BUILDING TOWARD:
A "plausible fiction" is a concrete, believable story. Not a wish list, not a vague dream — a story where someone could read it and think "yeah, that could actually happen."

OUTPUT:
Just respond naturally. No JSON, no special formatting."""

    tones = {
        "terse": """
TONE - TERSE:
- No pleasantries, no praise, no filler
- Questions only. Observations only. Assumptions stated directly.
- Skip "That's interesting" or "Great idea" — just ask the next question
- One short question or observation per response
- If something seems implausible, say so and suggest alternatives
- Never start with "I" — just get to the point
- Maximum 2-3 sentences per response""",

        "balanced": """
TONE - BALANCED:
- Brief acknowledgment, then substance
- "Got it. What's step one?"
- One question at a time
- Challenge directly but not harshly
- Skip excessive encouragement
- 2-4 sentences per response""",

        "friendly": """
TONE - FRIENDLY:
- Warm but efficient
- It's okay to show enthusiasm briefly
- Ask one question at a time (usually)
- Challenge them gently: "That sounds like a big leap — what makes you confident?"
- Encourage when warranted: "That's a solid first step"
- If they go off-topic, chat briefly, then steer back""",
    }
    return base + tones.get(chattiness, tones["terse"])


# Verbatim from src/llm/prompts.js — FLOWCHART_EXTRACTION_PROMPT
_EXTRACTION_SYSTEM = """You are a structured data extractor. You watch a conversation and extract a flowchart representing the user's goal and plan.

CRITICAL RULES:
1. ALWAYS extract a goal from the user's FIRST message. Every conversation has a goal.
2. Extract tasks/steps whenever the user describes HOW they would do something.
3. Be AGGRESSIVE about extraction — capture structure early, refine later.
4. Don't skip extraction because the plan is "incomplete" — partial plans are fine.
5. NEVER create a task that duplicates the goal. The goal is WHAT the user wants; tasks are HOW. If the user only states a goal with no steps, just setGoal — don't also addTask with the same label.

OUTPUT FORMAT (always valid JSON):
{
  "operations": [
    { "op": "setGoal", "label": "...", "source": { "userText": "...", "aiText": "...", "turn": 1 } },
    { "op": "addTask", "id": "...", "label": "...", "source": {...} }
  ]
}

AVAILABLE OPERATIONS:
- setGoal: Set the main goal (do this on turn 1!)
- setGoalSpec: Set the specification data (A type) for the goal
- setGoalOutcomes: Set custom outcomes (B type) for the goal
- addTask: Add a step. Use afterTask/beforeTask for ordering; parentId for nesting.
- updateTask: Refine a step's label
- removeTask: Remove a step
- setPlausibility: Mark confidence level (high/medium/low)
- revealParentGoal: When user reveals a deeper/bigger goal
- setTaskLikelihoods: Set likelihood for ALL outcomes of a task (must sum to 100)
- addTaskOutcome: Add an outcome to a task
- setValueCurrency: Set the currency for outcome values
- setOutcomeValues: Assign values to goal outcomes

TASK ORDERING AND NESTING:
- Use afterTask or beforeTask to insert in the right position.
- SUBTASKS (ELABORATION): When the user describes HOW to do an existing task, use parentId.
  { "op": "addTask", "id": "...", "label": "...", "parentId": "existing-task-id" }
- ELABORATION vs REPLACEMENT: prefer elaboration (parentId) unless the user explicitly abandons the approach.

WHAT TO EXTRACT:
- User states a goal → setGoal
- User specifies parameters/requirements → setGoalSpec
- User specifies distinct end-states → setGoalOutcomes
- User assigns value to outcomes → setValueCurrency + setOutcomeValues
- User describes a method/approach → addTask
- User says something is "hard" or uncertain → setPlausibility: "low"
- User refines a step → updateTask
- User abandons an approach → removeTask
- User reveals a deeper motivation → revealParentGoal
- User expresses confidence about an outcome → setTaskLikelihoods

SELF-CHECK:
Before outputting, mentally apply operations to the flowchart and narrate the story.
If it doesn't make sense, fix operations.
If genuinely confused: { "operations": [], "confusion": "brief description" }

DO NOT return empty operations unless the exchange is truly off-topic or you are genuinely confused.

Now extract from this conversation:"""


# Verbatim from src/llm/prompts.js — PER_TASK_EVALUATOR_PROMPT
_PER_TASK_EVALUATOR_SYSTEM = """You assess task plausibility. Be quick and honest.

You are given a TASK and a CONVERSATION. Assess the task's outcomes and likelihoods.

RULES:
- Return 2-3 outcomes (always Success and Failure, plus a notable alternative if relevant)
- Likelihoods must be integers summing to 100
- READ THE AI RESPONSES. If the AI raised doubts or pointed out problems, give LOW success likelihood
- If something is physically impossible as described, success = 1-10%
- Be calibrated: "easy" = 80-95%, "doable" = 50-79%, "hard" = 20-49%, "very unlikely" = 5-19%, "impossible" = 1-5%
- For parent tasks with subtasks, consider the subtask difficulty when assessing

JSON only, no explanation:
{"outcomes": [{"id": "outcome-success", "label": "Success", "likelihood": N}, {"id": "outcome-fail", "label": "Failure", "likelihood": N}]}"""


def _format_flowchart_for_display(flowchart: dict | None) -> str:
    if not flowchart:
        return "No story yet — this is the beginning of the conversation."
    goal = flowchart.get("goal")
    tasks = flowchart.get("tasks") or []
    if not goal and not tasks:
        return "No story yet — this is the beginning of the conversation."

    lines = []
    if goal:
        lines.append(f"Goal: {goal}")
    if tasks:
        children_of: dict[str | None, list] = {}
        for t in tasks:
            pid = t.get("parentId")
            children_of.setdefault(pid, []).append(t)

        def fmt_task(task: dict, indent: str) -> str:
            plaus = f" [{task['plausibility']} confidence]" if task.get("plausibility") else ""
            line = f"{indent}- [{task['id']}] {task['label']}{plaus}\n"
            for o in task.get("outcomes") or []:
                lstr = f" ({o['likelihood']}%)" if o.get("likelihood") is not None else ""
                line += f"{indent}    → {o['label']}{lstr}\n"
            for child in children_of.get(task["id"], []):
                line += fmt_task(child, indent + "  ")
            return line

        lines.append("\nSteps so far:")
        for root_task in children_of.get(None, []):
            lines.append(fmt_task(root_task, "  "))
    return "\n".join(lines)


def _format_conversation_for_extraction(messages: list[dict]) -> str:
    parts = []
    for i, m in enumerate(messages):
        role = "User" if m.get("role") == "user" else "AI"
        turn = i // 2 + 1
        parts.append(f"[Turn {turn}] {role}: {m.get('content', '')}")
    return "\n\n".join(parts)


# =============================================================================
# POST /api/flowchart/converse
# =============================================================================

@router.post("/flowchart/converse", response_model=ConverseResponse)
async def converse(
    body: ConverseRequest,
    user_and_token: Annotated[Optional[tuple[dict, str]], Depends(_get_optional_user)],
):
    """
    Two LLMs in parallel:
      1. Conversational (Sonnet) — returns natural dialogue response
      2. Extraction (Sonnet) — returns structured operations to update the flowchart
    """
    messages = [m.model_dump() for m in body.messages]
    state_dict = body.current_state.model_dump() if body.current_state else None

    flowchart_display = _format_flowchart_for_display(state_dict)

    # Conversation system prompt
    conv_system = _get_conversation_prompt(body.chattiness)
    conv_system += f"\n\nCURRENT STATE OF THEIR STORY:\n{flowchart_display}"
    if body.previous_confusion:
        conv_system += (
            f'\n\nNOTE: The system had trouble structuring the user\'s last response. '
            f'The issue was: "{body.previous_confusion}". '
            f'Consider asking a clarifying question to resolve this.'
        )

    # Extraction user message
    conversation_text = _format_conversation_for_extraction(messages)
    current_turn = len(messages) // 2 + 1
    extraction_user = (
        f"CURRENT FLOWCHART STATE:\n{json.dumps(state_dict, indent=2)}\n\n"
        f"CONVERSATION SO FAR:\n{conversation_text}\n\n"
        f"The latest exchange is turn {current_turn}. Extract any flowchart updates from this conversation.\n"
        f"Output JSON with an \"operations\" array. Include \"source\" with verbatim quotes for each operation."
    )

    conv_task = _call_anthropic(_get_conversation_prompt(body.chattiness) + f"\n\nCURRENT STATE OF THEIR STORY:\n{flowchart_display}", messages, _CONVERSATION_MODEL)
    extract_task = _call_anthropic(_EXTRACTION_SYSTEM, [{"role": "user", "content": extraction_user}], _EXTRACTION_MODEL)

    conversation_response, extraction_raw = await asyncio.gather(conv_task, extract_task)

    operations, confusion = _parse_operations(extraction_raw)

    return ConverseResponse(
        ai_response=conversation_response,
        operations=operations,
        confusion=confusion,
    )


# =============================================================================
# POST /api/flowchart/evaluate
# =============================================================================

@router.post("/flowchart/evaluate", response_model=EvaluateResponse)
async def evaluate(
    body: EvaluateRequest,
    user_and_token: Annotated[Optional[tuple[dict, str]], Depends(_get_optional_user)],
):
    """
    Per-task evaluator: assigns likelihood distributions to tasks that lack them.
    Runs one Haiku call per task in parallel (mirrors the JS architecture).
    """
    tasks = body.current_state.tasks or []
    conversation_summary = body.conversation_summary

    tasks_needing_eval = [
        t for t in tasks
        if not t.outcomes or any(o.likelihood is None for o in t.outcomes)
    ]

    if not tasks_needing_eval:
        return EvaluateResponse(operations=[])

    children_of: dict[str | None, list] = {}
    for t in tasks:
        children_of.setdefault(t.parentId, []).append(t)

    async def eval_one_task(task) -> list[dict[str, Any]]:
        children = children_of.get(task.id, [])
        prompt = f'TASK: "{task.label}"'
        if children:
            child_labels = ", ".join(c.label for c in children)
            prompt += (
                f"\nThis task has subtasks: {child_labels}. "
                f"If subtasks have notable alternative outcomes, include a matching outcome on this parent."
            )
        prompt += f"\n\nCONVERSATION:\n{conversation_summary}\n\nAssess this task. JSON only:"

        try:
            raw = await _call_anthropic(
                _PER_TASK_EVALUATOR_SYSTEM,
                [{"role": "user", "content": prompt}],
                _EVALUATOR_MODEL,
            )
            match = re.search(r"\{[\s\S]*\}", raw)
            if match:
                parsed = json.loads(match.group())
                outcomes = parsed.get("outcomes", [])
                ops: list[dict] = []
                for o in outcomes:
                    ops.append({"op": "addTaskOutcome", "taskId": task.id, "id": o["id"], "label": o["label"]})
                likelihoods = {o["id"]: o.get("likelihood") for o in outcomes}
                ops.append({"op": "setTaskLikelihoods", "taskId": task.id, "likelihoods": likelihoods})
                return ops
        except Exception:
            pass
        return []

    results = await asyncio.gather(*[eval_one_task(t) for t in tasks_needing_eval])
    all_ops = [op for task_ops in results for op in task_ops]

    return EvaluateResponse(operations=all_ops)


# =============================================================================
# GET /api/posts/{post_id}/flowchart
# =============================================================================

@router.get("/posts/{post_id}/flowchart", response_model=FlowchartResponse)
async def get_flowchart(
    post_id: str,
    user_and_token: Annotated[tuple[dict, str], Depends(get_current_user_and_token)],
):
    _, access_token = user_and_token
    with httpx.Client(timeout=15.0) as client:
        r = client.get(
            _rest_url(f"/posts?id=eq.{post_id}&select=id,flowchart_json"),
            headers=_supabase_headers(access_token),
        )
    if r.status_code == 404 or (r.status_code == 200 and not r.json()):
        raise HTTPException(status_code=404, detail="Post not found.")
    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail="Failed to fetch post.")
    rows = r.json()
    if not rows:
        raise HTTPException(status_code=404, detail="Post not found.")
    row = rows[0]
    return FlowchartResponse(post_id=post_id, flowchart_json=row.get("flowchart_json"))


# =============================================================================
# PUT /api/posts/{post_id}/flowchart
# =============================================================================

@router.put("/posts/{post_id}/flowchart", response_model=FlowchartResponse)
async def save_flowchart(
    post_id: str,
    body: FlowchartSaveRequest,
    user_and_token: Annotated[tuple[dict, str], Depends(get_current_user_and_token)],
):
    """
    Persist a flowchart. Runs structural validation (Inv 1–5) before writing.
    Full validation (Inv 6–11, L1–L3) is the client's responsibility.
    """
    # Structural validation gate
    errors = validate_flowchart_structure(body.flowchart_json)
    if errors:
        raise HTTPException(
            status_code=422,
            detail={"message": "Flowchart failed structural validation.", "errors": errors},
        )

    _, access_token = user_and_token
    with httpx.Client(timeout=15.0) as client:
        r = client.patch(
            _rest_url(f"/posts?id=eq.{post_id}"),
            headers={**_supabase_headers(access_token), "Prefer": "return=representation"},
            json={"flowchart_json": body.flowchart_json},
        )
    if r.status_code == 404 or (r.status_code == 200 and not r.json()):
        raise HTTPException(status_code=404, detail="Post not found or not owned by you.")
    if r.status_code not in (200, 204):
        raise HTTPException(status_code=r.status_code, detail="Failed to save flowchart.")

    return FlowchartResponse(post_id=post_id, flowchart_json=body.flowchart_json)
