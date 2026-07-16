"""The two-chamber council: Assembly proposes & debates, Governing Body ratifies.

``run_council`` is an async generator that yields event dicts as each stage
completes, which the web layer forwards to the browser as Server-Sent Events.
It depends only on an injected ``chat`` callable (``async (model, messages) ->
str``), so the whole flow can be exercised offline with a stub.
"""

from __future__ import annotations

import asyncio
import re
from typing import AsyncIterator, Awaitable, Callable

from . import prompts

ChatFn = Callable[[str, list[dict]], Awaitable[str]]

LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_VOTE_RE = re.compile(r"VOTE:\s*([A-Za-z])", re.IGNORECASE)


async def _safe_chat(chat: ChatFn, model: str, messages: list[dict]) -> tuple[bool, str]:
    """Call ``chat`` without letting one model's failure abort the round."""
    try:
        text = await chat(model, messages)
        return True, text
    except Exception as exc:  # noqa: BLE001 - deliberately broad; we degrade gracefully
        return False, str(exc)


def _answers_block(proposals: list[dict]) -> str:
    return "\n\n".join(f"Answer {p['label']}:\n{p['answer']}" for p in proposals)


def _debate_block(debates: list[dict]) -> str:
    if not debates:
        return "(no debate contributions)"
    return "\n\n".join(f"Assembly member {i + 1} argued:\n{d['critique']}"
                       for i, d in enumerate(debates))


def parse_vote(text: str, valid_labels: set[str]) -> str | None:
    """Return the last valid ``VOTE: X`` letter in ``text``, else None."""
    matches = _VOTE_RE.findall(text or "")
    for raw in reversed(matches):
        label = raw.upper()
        if label in valid_labels:
            return label
    return None


def decide_winner(tally: dict[str, int]) -> str:
    """Highest vote count wins; ties break to the lowest letter (deterministic)."""
    return min(tally, key=lambda label: (-tally[label], label))


async def run_council(
    question: str,
    assembly_models: list[str],
    governing_models: list[str],
    chat: ChatFn,
) -> AsyncIterator[dict]:
    """Drive the full council and yield UI events as each stage resolves."""

    # ---- Round 1: Assembly proposes -------------------------------------
    yield {"type": "stage", "name": "proposals"}
    results = await asyncio.gather(
        *(_safe_chat(chat, m, prompts.propose_messages(question)) for m in assembly_models)
    )
    proposals: list[dict] = []
    for model, (ok, text) in zip(assembly_models, results):
        if ok:
            proposals.append({"model": model, "answer": text.strip()})
        else:
            yield {"type": "model_error", "stage": "proposals", "model": model, "message": text}

    if len(proposals) < 2:
        yield {
            "type": "aborted",
            "message": "Need at least two Assembly answers to hold a debate; "
                       f"only {len(proposals)} model(s) responded.",
        }
        return

    for i, p in enumerate(proposals):
        p["label"] = LETTERS[i]
        yield {"type": "proposal", "label": p["label"], "model": p["model"], "answer": p["answer"]}

    answers_block = _answers_block(proposals)

    # ---- Round 2: Assembly debates --------------------------------------
    yield {"type": "stage", "name": "debate"}
    debate_results = await asyncio.gather(
        *(_safe_chat(chat, p["model"], prompts.debate_messages(question, answers_block))
          for p in proposals)
    )
    debates: list[dict] = []
    for p, (ok, text) in zip(proposals, debate_results):
        if ok:
            debates.append({"model": p["model"], "critique": text.strip()})
            yield {"type": "debate", "model": p["model"], "critique": text.strip()}
        else:
            yield {"type": "model_error", "stage": "debate", "model": p["model"], "message": text}

    debate_block = _debate_block(debates)
    labels = [p["label"] for p in proposals]

    # ---- Round 3: Governing Body votes ----------------------------------
    yield {"type": "stage", "name": "vote"}
    vote_results = await asyncio.gather(
        *(_safe_chat(chat, j, prompts.vote_messages(question, answers_block, debate_block, labels))
          for j in governing_models)
    )
    tally: dict[str, int] = {label: 0 for label in labels}
    valid = set(labels)
    total_votes = 0
    for judge, (ok, text) in zip(governing_models, vote_results):
        if not ok:
            yield {"type": "model_error", "stage": "vote", "model": judge, "message": text}
            continue
        label = parse_vote(text, valid)
        reason = _first_sentence(text)
        if label is not None:
            tally[label] += 1
            total_votes += 1
        yield {
            "type": "vote",
            "judge": judge,
            "vote": label,          # may be None = abstention
            "reason": reason,
            "raw": text.strip(),
        }

    # ---- Ratification ----------------------------------------------------
    winner_label = decide_winner(tally)
    winner = next(p for p in proposals if p["label"] == winner_label)
    yield {
        "type": "result",
        "winner_label": winner_label,
        "winner_model": winner["model"],
        "winner_answer": winner["answer"],
        "tally": tally,
        "total_votes": total_votes,
        "ratified": total_votes > 0,
    }


def _first_sentence(text: str) -> str:
    """A short justification: the first non-vote line, trimmed."""
    for line in (text or "").splitlines():
        line = line.strip()
        if line and not line.upper().startswith("VOTE:"):
            return line[:280]
    return ""
