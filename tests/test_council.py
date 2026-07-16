"""Offline tests for the council orchestration — no network, no API key.

A stub ``chat`` stands in for OpenRouter. It inspects the system prompt to tell
which round it is being asked for, and returns canned text (including a
controllable vote per judge), so we can assert the full pipeline's behaviour.
"""

from __future__ import annotations

import pytest

from app.council import decide_winner, parse_vote, run_council


class StubChat:
    """Configurable stand-in for OpenRouterClient.chat.

    Parameters
    ----------
    votes:  maps a judge model id -> the letter it will vote for (or raw text).
    fail_on: set of (model, stage) pairs that should raise, to test resilience.
    """

    def __init__(self, votes: dict[str, str], fail_on: set[tuple[str, str]] | None = None):
        self.votes = votes
        self.fail_on = fail_on or set()
        self.calls: list[tuple[str, str]] = []  # (model, stage)

    @staticmethod
    def _stage(messages: list[dict]) -> str:
        system = messages[0]["content"]
        if "judge on the Governing Body" in system:
            return "vote"
        if "several answers" in system.lower():
            return "debate"
        return "propose"

    async def __call__(self, model: str, messages: list[dict]) -> str:
        stage = self._stage(messages)
        self.calls.append((model, stage))
        if (model, stage) in self.fail_on:
            raise RuntimeError(f"{model} exploded during {stage}")
        if stage == "propose":
            return f"Proposal authored by {model}."
        if stage == "debate":
            return f"{model} thinks answer A has merit."
        # vote
        letter = self.votes.get(model, "A")
        return f"Answer {letter} is the most rigorous.\nVOTE: {letter}"


async def _collect(gen) -> list[dict]:
    return [ev async for ev in gen]


def _events_of(events, type_):
    return [e for e in events if e["type"] == type_]


# --- unit-level helpers ---------------------------------------------------

def test_parse_vote_takes_last_valid_letter():
    assert parse_vote("blah VOTE: q\nVOTE: B", {"A", "B"}) == "B"
    assert parse_vote("VOTE: Z", {"A", "B"}) is None
    assert parse_vote("no vote here", {"A", "B"}) is None
    assert parse_vote("i choose vote: a", {"A", "B"}) == "A"


def test_decide_winner_breaks_ties_to_lowest_letter():
    assert decide_winner({"A": 2, "B": 1}) == "A"
    assert decide_winner({"A": 1, "B": 1, "C": 1}) == "A"  # tie -> lowest
    assert decide_winner({"A": 0, "B": 3, "C": 1}) == "B"


# --- full-flow tests ------------------------------------------------------

ASSEMBLY = ["model/alpha", "model/bravo", "model/charlie"]
GOVERNING = ["judge/one", "judge/two", "judge/three"]


async def test_happy_path_labels_votes_and_winner():
    # A=alpha, B=bravo, C=charlie. Two judges pick B, one picks A -> B wins.
    stub = StubChat(votes={"judge/one": "B", "judge/two": "B", "judge/three": "A"})
    events = await _collect(run_council("What is virtue?", ASSEMBLY, GOVERNING, stub))

    proposals = _events_of(events, "proposal")
    assert [p["label"] for p in proposals] == ["A", "B", "C"]
    assert proposals[0]["model"] == "model/alpha"

    # Only governing models cast votes.
    votes = _events_of(events, "vote")
    assert {v["judge"] for v in votes} == set(GOVERNING)
    assert all(v["judge"] not in ASSEMBLY for v in votes)

    result = _events_of(events, "result")[0]
    assert result["winner_label"] == "B"
    assert result["winner_model"] == "model/bravo"
    assert result["tally"] == {"A": 1, "B": 2, "C": 0}
    assert result["total_votes"] == 3
    assert result["ratified"] is True


async def test_failing_debater_is_dropped_without_crashing():
    # charlie fails to propose -> only A/B remain, council still completes.
    stub = StubChat(
        votes={"judge/one": "A", "judge/two": "B", "judge/three": "A"},
        fail_on={("model/charlie", "propose")},
    )
    events = await _collect(run_council("Q?", ASSEMBLY, GOVERNING, stub))

    labels = [p["label"] for p in _events_of(events, "proposal")]
    assert labels == ["A", "B"]  # contiguous, no gap
    errors = _events_of(events, "model_error")
    assert any(e["model"] == "model/charlie" and e["stage"] == "propose" for e in errors) or \
           any(e["model"] == "model/charlie" for e in errors)

    result = _events_of(events, "result")[0]
    assert result["winner_label"] == "A"  # A:2 vs B:1
    assert result["ratified"] is True


async def test_tie_breaks_to_lowest_letter():
    # One judge each for A and B -> tie -> A wins deterministically.
    stub = StubChat(votes={"judge/one": "A", "judge/two": "B"})
    events = await _collect(run_council("Q?", ASSEMBLY, ["judge/one", "judge/two"], stub))
    result = _events_of(events, "result")[0]
    assert result["tally"]["A"] == 1 and result["tally"]["B"] == 1
    assert result["winner_label"] == "A"


async def test_abstention_when_vote_unparseable():
    class Abstainer(StubChat):
        async def __call__(self, model, messages):
            if self._stage(messages) == "vote":
                self.calls.append((model, "vote"))
                return "I cannot decide."  # no VOTE line
            return await super().__call__(model, messages)

    stub = Abstainer(votes={})
    events = await _collect(run_council("Q?", ASSEMBLY, ["judge/one"], stub))
    result = _events_of(events, "result")[0]
    assert result["total_votes"] == 0
    assert result["ratified"] is False  # nobody cast a valid vote
    # A winner is still reported deterministically (lowest label).
    assert result["winner_label"] == "A"


async def test_aborts_when_too_few_proposals():
    stub = StubChat(votes={}, fail_on={("model/bravo", "propose"), ("model/charlie", "propose")})
    events = await _collect(run_council("Q?", ASSEMBLY, GOVERNING, stub))
    assert _events_of(events, "aborted")
    assert not _events_of(events, "result")
    # No votes should have been requested if the debate never happened.
    assert not any(stage == "vote" for _, stage in stub.calls)
