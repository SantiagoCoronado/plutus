"""Strategy-from-content translator (spec §13.5).

Pasted article/transcript/description → the EXISTING strategy-backtest AST —
never free-form code — plus a mandatory fidelity report: the plain-English
understanding and an explicit list of everything the source said that the
engine cannot express. A silent approximation is a bug; the user confirms the
draft before anything runs.
"""

from __future__ import annotations

import json

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.llm.base import AgentLoopProvider, ChatProvider
from app.llm.budget import ensure_budget
from app.llm.types import Message, Usage

log = get_logger(__name__)

MAX_CONTENT_CHARS = 60_000
KNOB_BOUNDS = {
    "stop_loss_pct": (0.0, 0.5),
    "take_profit_pct": (0.0, 2.0),
    "position_size_pct": (0.01, 1.0),
    "fees_pct": (0.0, 0.05),
}


def translator_system() -> str:
    from app.backtest.strategy import STRATEGY_FIELDS

    fields = ", ".join(sorted(STRATEGY_FIELDS))
    return f"""You translate investing/trading strategy descriptions into a \
machine-checkable backtest spec for a daily-bar, single-asset engine. You \
never write code — only the JSON described below.

CONDITION GRAMMAR (entry and exit are each one condition node):
- leaf: {{"field": <field>, "op": <op>, "value": <number | [low, high] | \
{{"field": <other field>}}>}}
- ops: > < >= <= == != between is_null not_null, plus crosses_above and \
crosses_below (value may reference another field, e.g. sma_50 crossing \
sma_200)
- combinators: {{"all": [nodes]}}, {{"any": [nodes]}}, {{"not": node}}
- fields: {fields}
- candlestick patterns (bullish_engulfing, bearish_engulfing, hammer, \
shooting_star, doji) are 1.0/0.0 series — use e.g. \
{{"field": "hammer", "op": "==", "value": 1}}

OPTIONAL KNOBS in "spec": stop_loss_pct (0-0.5), take_profit_pct (0-2), \
position_size_pct (0.01-1), fees_pct (0-0.05), start, end (ISO dates).

THE FIDELITY CONTRACT — the single most important rule: anything in the \
source you cannot express EXACTLY in this grammar MUST be listed in \
"limitations" in plain English (intraday/4-hour rules, options legs, \
multi-asset pairs or rotation, position pyramiding, fundamental data, \
discretionary judgment, trailing stops, indicators with parameters the \
grammar's fields don't offer, etc.). A silent approximation is a bug. If the \
core of the strategy cannot be expressed at all, set "translatable": false \
and explain why in "limitations".

Respond with ONLY this JSON object, no prose, no code fences:
{{
  "translatable": true | false,
  "symbol": "<ticker the source is about, or the provided default>",
  "understanding_md": "<2-6 sentences: buy when X, sell when Y, exit at Z>",
  "limitations": ["<everything inexpressible or approximated>", ...],
  "spec": {{"entry": <node>, "exit": <node>, ...optional knobs}}
}}"""


def translator_user_prompt(content: str, symbol_hint: str | None) -> str:
    hint = (
        f'If the source names no specific asset, use "{symbol_hint}".'
        if symbol_hint
        else "If the source names no specific asset, pick the most sensible liquid "
        'ticker it implies (e.g. "SPY" for generic index strategies) and add a '
        "limitation noting the choice."
    )
    return f"{hint}\n\nSource content to translate:\n\n{content}"


async def _one_shot(provider, system: str, prompt: str) -> tuple[str, Usage]:
    """One structured completion from either provider protocol."""
    if isinstance(provider, AgentLoopProvider):
        parts: list[str] = []
        usage = Usage()
        async for event in provider.run_loop(
            system=system, user_message=prompt, tools=[],
            conversation_id=0, session_id=None, max_turns=1,
        ):
            if event.type == "text_delta":
                parts.append(str(event.data.get("text", "")))
            elif event.type == "done":
                raw = event.data.get("usage") or {}
                usage = Usage(int(raw.get("input_tokens") or 0),
                              int(raw.get("output_tokens") or 0))
            elif event.type == "error":
                from app.llm.base import LLMError

                raise LLMError(str(event.data.get("message")))
        return "".join(parts), usage
    assert isinstance(provider, ChatProvider)
    response = await provider.chat(
        [Message(role="user", content=prompt)], system=system, max_tokens=4096
    )
    return response.text or "", response.usage


def _extract_json(text: str) -> dict:
    """The strict prompt still occasionally gets fences or prose — dig out the object."""
    text = text.strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in the model response")
    return json.loads(text[start : end + 1])


def _validate_spec(spec: dict) -> list[str]:
    """Server-side re-validation — never trust the model. Returns error strings."""
    from app.backtest.strategy import parse_condition
    from app.screener.ast import AstError

    errors: list[str] = []
    if not isinstance(spec, dict):
        return ["spec must be an object"]
    for part in ("entry", "exit"):
        node = spec.get(part)
        if not isinstance(node, dict):
            errors.append(f"spec.{part} is required and must be a condition node")
            continue
        try:
            parse_condition(node)
        except AstError as exc:
            errors.append(f"spec.{part}: {exc.errors}")
    for knob, (low, high) in KNOB_BOUNDS.items():
        value = spec.get(knob)
        if value is None:
            continue
        if not isinstance(value, int | float) or not (low <= float(value) <= high):
            errors.append(f"spec.{knob} must be a number in [{low}, {high}]")
    return errors


def _resolve_symbol(session: Session, symbol: str | None):
    from app.models import Asset

    if not symbol:
        return None
    return session.scalar(
        select(Asset)
        .where(func.upper(Asset.symbol) == symbol.strip().upper(), Asset.is_active)
        .limit(1)
    )


async def translate_strategy_content(
    session: Session, content: str, symbol_hint: str | None = None
):
    """Run the translation pipeline; returns the persisted StrategyTranslation."""
    from app.llm.providers import get_provider
    from app.models import AgentConversation, AgentMessage, StrategyTranslation

    content = content.strip()[:MAX_CONTENT_CHARS]
    ensure_budget(session)
    provider = get_provider(session)

    conversation = AgentConversation(
        kind="translate", status="running", autonomous=True,
        title="Strategy translation", provider=provider.name, model=provider.model,
    )
    session.add(conversation)
    session.flush()

    translation = StrategyTranslation(
        conversation_id=conversation.id,
        source_content=content,
        provider=provider.name,
        model=provider.model,
    )
    session.add(translation)
    session.flush()

    system = translator_system()
    prompt = translator_user_prompt(content, symbol_hint)
    total_usage = Usage()
    parsed: dict | None = None
    errors: list[str] = []

    for attempt in (1, 2):  # one retry, with the validation errors appended
        text, usage = await _one_shot(provider, system, prompt)
        total_usage = total_usage + usage
        session.add(AgentMessage(
            conversation_id=conversation.id, role="assistant", content=text,
            provider=provider.name, model=provider.model,
            input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
        ))
        session.flush()
        try:
            candidate = _extract_json(text)
        except (ValueError, json.JSONDecodeError) as exc:
            errors = [f"response was not valid JSON: {exc}"]
        else:
            if candidate.get("translatable") is False:
                parsed, errors = candidate, []
                break
            errors = _validate_spec(candidate.get("spec") or {})
            if not errors:
                parsed = candidate
                break
        if attempt == 1:
            prompt = (
                f"{prompt}\n\nYour previous response failed validation:\n"
                + "\n".join(f"- {e}" for e in errors)
                + "\nRespond again with ONLY the corrected JSON object."
            )
            log.info("translator_retry", errors=errors)

    if parsed is None:
        translation.status = "failed"
        translation.error = "; ".join(errors) or "translation failed"
        conversation.status = "error"
        conversation.error = translation.error
        session.commit()
        return translation

    translation.translatable = bool(parsed.get("translatable", True))
    translation.understanding_md = str(parsed.get("understanding_md") or "") or None
    limitations = parsed.get("limitations") or []
    translation.limitations = [str(item) for item in limitations]
    translation.symbol = (str(parsed.get("symbol") or "").upper() or None)

    if translation.translatable:
        translation.spec = parsed.get("spec")
        asset = _resolve_symbol(session, translation.symbol)
        if asset is not None:
            translation.asset_id = asset.id
        else:
            translation.limitations = [
                *translation.limitations,
                f"'{translation.symbol}' is not tracked in the hub — track the asset "
                "first, then confirm the backtest",
            ]
    translation.status = "draft"
    conversation.status = "done"
    session.commit()
    return translation
