"""Prompt templates for each role and round of the council.

Two roles:
  * Assembly (Ekklesia) members  — propose an answer, then debate.
  * Governing Body (Boule) judges — read the debate and vote to ratify.
"""

from __future__ import annotations

# --- Round 1: Assembly members propose -----------------------------------

PROPOSE_SYSTEM = (
    "You are a member of the Assembly in an AI Council modelled on Athenian "
    "democracy. You have been asked to answer a question or complete a task. "
    "Give your own best, self-contained answer. Be clear, substantive and "
    "concise — you are trying to persuade a governing body that yours is the "
    "strongest answer, so lead with your reasoning and conclusion, not filler."
)


def propose_messages(question: str) -> list[dict]:
    return [
        {"role": "system", "content": PROPOSE_SYSTEM},
        {"role": "user", "content": question.strip()},
    ]


# --- Round 2: Assembly members debate ------------------------------------

DEBATE_SYSTEM = (
    "You are a member of the Assembly in an AI Council. Several answers to the "
    "same question have been proposed. They are shown to you anonymously, "
    "labelled by letter. Critically debate them: name the strongest and weakest "
    "points of each, correct any errors, and argue which single answer the "
    "governing body should ratify. Keep it focused — a few tight paragraphs."
)


def debate_messages(question: str, answers_block: str) -> list[dict]:
    user = (
        f"The question put to the Assembly was:\n\n{question.strip()}\n\n"
        f"The proposed answers are:\n\n{answers_block}\n\n"
        "Debate these answers and argue which is strongest."
    )
    return [
        {"role": "system", "content": DEBATE_SYSTEM},
        {"role": "user", "content": user},
    ]


# --- Round 3: Governing Body votes ---------------------------------------

VOTE_SYSTEM = (
    "You are a judge on the Governing Body of an AI Council — an impartial "
    "magistrate. You did NOT write any of the answers. Weigh the anonymous, "
    "lettered answers together with the Assembly's debate, then ratify exactly "
    "one answer as the council's proposal.\n\n"
    "Reply with ONE short sentence of justification, then a FINAL line in "
    "exactly this format and nothing after it:\n"
    "VOTE: <letter>\n"
    "You must choose one of the available letters."
)


def vote_messages(question: str, answers_block: str, debate_block: str, labels: list[str]) -> list[dict]:
    user = (
        f"The question put to the Council was:\n\n{question.strip()}\n\n"
        f"The proposed answers are:\n\n{answers_block}\n\n"
        f"The Assembly's debate:\n\n{debate_block}\n\n"
        f"Available choices: {', '.join(labels)}.\n"
        "Cast your vote now."
    )
    return [
        {"role": "system", "content": VOTE_SYSTEM},
        {"role": "user", "content": user},
    ]
