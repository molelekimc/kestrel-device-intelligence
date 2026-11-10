"""Claude tool-use loop over the governed fleet tools.

The model sees exactly the tools the MCP server exposes (same names, descriptions
and input schemas), and every call runs through FleetTools, so the briefing and
the evals inherit the same validation, suppression, identifier checks and audit
log as Claude Desktop. The tools are read-only; there is nothing to remediate with."""

import asyncio
import json
import os
from dataclasses import dataclass, field

import anthropic

from mcp_server.guardrails import IdentifierLeak, ToolInputError
from mcp_server.tools import FleetTools

DEFAULT_MODEL = os.environ.get("KESTREL_MODEL", "claude-opus-5-5")
# Models that accept server-side refusal fallback (`fallbacks: "default"`).
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-fable-5", "claude-sonnet-5-5"}
MAX_TURNS = 16


@dataclass
class LoopResult:
    text: str
    model: str
    tool_calls: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0})
    stop_reason: str = ""


class BriefingModelError(RuntimeError):
    pass


def tool_definitions() -> list[dict]:
    """Anthropic tool definitions generated from the MCP server's own tool list."""
    from mcp_server.server import server
    listed = asyncio.run(server.list_tools())
    return [{"name": t.name, "description": t.description or "", "input_schema": t.input_schema}
            for t in listed]


def _execute(tools: FleetTools, name: str, args: dict) -> tuple[str, bool]:
    fn = getattr(tools, name, None)
    if fn is None or name.startswith("_"):
        return f"Unknown tool {name}", True
    try:
        return json.dumps(fn(**args), default=str), False
    except (ToolInputError, IdentifierLeak) as e:
        return str(e), True
    except TypeError as e:          # unexpected argument names
        return f"Invalid arguments: {e}", True


def run(system: str, prompt: str, *, tools: FleetTools, model: str | None = None,
        client=None, max_turns: int = MAX_TURNS, effort: str = "high") -> LoopResult:
    model = model or DEFAULT_MODEL
    client = client or anthropic.Anthropic()
    defs = tool_definitions()
    messages: list[dict] = [{"role": "user", "content": prompt}]
    result = LoopResult(text="", model=model)
    extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"} \
        if model in FALLBACK_MODELS else {}

    for _ in range(max_turns):
        try:
            response = client.beta.messages.create(
                model=model, max_tokens=16000, system=system, tools=defs, messages=messages,
                output_config={"effort": effort}, **extra)
        except TypeError as e:
            if "authentication" not in str(e):
                raise
            raise BriefingModelError("No Anthropic credentials found. Set ANTHROPIC_API_KEY.") from e
        except anthropic.AuthenticationError as e:
            raise BriefingModelError("ANTHROPIC_API_KEY was rejected.") from e
        except anthropic.NotFoundError as e:
            raise BriefingModelError(f"Model '{model}' not found; check --model / KESTREL_MODEL.") from e
        except anthropic.APIStatusError as e:
            raise BriefingModelError(f"Claude API error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise BriefingModelError("Could not reach the Claude API (network).") from e
        result.usage["input_tokens"] += response.usage.input_tokens
        result.usage["output_tokens"] += response.usage.output_tokens
        result.stop_reason = response.stop_reason
        result.model = response.model or model

        if response.stop_reason == "refusal":
            raise BriefingModelError("The model declined the request (stop_reason=refusal); no draft written.")
        # Append the full content (thinking blocks included) so history stays append-only.
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason == "max_tokens":
            raise BriefingModelError("The response hit max_tokens before finishing; no draft written.")
        if response.stop_reason != "tool_use":
            result.text = "\n".join(b.text for b in response.content if b.type == "text").strip()
            return result

        outputs = []
        for block in (b for b in response.content if b.type == "tool_use"):
            content, is_error = _execute(tools, block.name, dict(block.input))
            result.tool_calls.append({"tool": block.name, "args": dict(block.input), "ok": not is_error})
            outputs.append({"type": "tool_result", "tool_use_id": block.id,
                            "content": content, "is_error": is_error})
        messages.append({"role": "user", "content": outputs})

    raise BriefingModelError(f"No final answer after {max_turns} turns; no draft written.")
