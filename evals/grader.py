"""Grading: deterministic rules on the answer text, plus an LLM grader for the rubric."""

import re

import anthropic
from pydantic import BaseModel

from automation.llm import DEFAULT_MODEL, FALLBACK_MODELS


def apply_rules(answer: str, rules: dict | None) -> list[dict]:
    """Each `all_of` group needs one matching pattern; no `none_of` pattern may match."""
    results = []
    for group in (rules or {}).get("all_of", []):
        hit = next((p for p in group if re.search(p, answer, re.IGNORECASE | re.DOTALL)), None)
        results.append({"rule": "mentions one of: " + " | ".join(group), "pass": hit is not None})
    for pattern in (rules or {}).get("none_of", []):
        m = re.search(pattern, answer, re.IGNORECASE)
        results.append({"rule": f"does not say: {pattern}", "pass": m is None,
                        **({"found": m.group(0)} if m else {})})
    return results


class Criterion(BaseModel):
    criterion: str
    passed: bool
    reason: str


class Grade(BaseModel):
    criteria: list[Criterion]


GRADER_SYSTEM = """You grade answers from a fleet-analytics assistant for a fictional client.
For each rubric criterion decide pass or fail, judging only the answer text. Use the reference
facts to check numbers; small rounding differences are fine. A criterion about labelling
something as a hypothesis passes if the answer clearly frames the cause as likely, suspected or
to be confirmed. Quote or paraphrase the part of the answer that justifies each decision in
`reason`. Return one entry per criterion, in the given order, with the criterion text copied
exactly."""


def llm_grade(question: str, answer: str, rubric: list[str], reference: list[str],
              model: str | None = None, client=None) -> list[dict]:
    model = model or DEFAULT_MODEL
    client = client or anthropic.Anthropic()
    extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"} \
        if model in FALLBACK_MODELS else {}
    prompt = (f"Question:\n{question}\n\nAnswer to grade:\n<answer>\n{answer}\n</answer>\n\n"
              "Reference facts (from the governed tools):\n" + "\n".join(f"- {r}" for r in reference) +
              "\n\nRubric criteria:\n" + "\n".join(f"{i + 1}. {c}" for i, c in enumerate(rubric)))
    response = client.beta.messages.parse(
        model=model, max_tokens=4000, system=GRADER_SYSTEM, output_format=Grade,
        messages=[{"role": "user", "content": prompt}], **extra)
    if response.stop_reason == "refusal" or response.parsed_output is None:
        return [{"criterion": c, "pass": False, "reason": "grader returned no verdict"} for c in rubric]
    by_text = {c.criterion.strip(): c for c in response.parsed_output.criteria}
    out = []
    for i, c in enumerate(rubric):
        g = by_text.get(c.strip()) or (response.parsed_output.criteria[i]
                                       if i < len(response.parsed_output.criteria) else None)
        out.append({"criterion": c, "pass": bool(g and g.passed), "reason": g.reason if g else "missing"})
    return out
