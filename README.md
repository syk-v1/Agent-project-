# 🏛️ AI Council

An **assembly of free AI models** that deliberate like an Athenian democracy.

You pose a question. A chamber of debater models each proposes an answer and
argues its case; a **separate** governing body of different models then reads the
debate and **votes to ratify** the strongest proposal. It all happens in a web
interface themed as an Athenian agora — marble, bronze, ostraka, and a laurel
crown for the winning answer.

- **Only free models.** It uses [OpenRouter](https://openrouter.ai)'s free tier
  and automatically lists the models that cost nothing to run.
- **Light on your device.** All the AI runs in OpenRouter's cloud — your machine
  only runs a tiny Python web server. No GPU, no local models.

---

## How the council works

The models are split into two chambers, and the process runs in three rounds:

| Round | Chamber | What happens |
|------:|---------|--------------|
| 1 · **Propose** | The Assembly (Ekklesia) | Each debater model answers the question independently. |
| 2 · **Debate**  | The Assembly | Answers are shown to everyone *anonymously* (labelled A, B, C…) and each debater critiques them, arguing which is strongest. |
| 3 · **Ratify**  | The Governing Body (Boule) | A *separate* set of judge models reads the answers + debate and each casts one vote. Most votes wins and is **ratified**. |

Using different models as judges means no one votes for their own answer, and
answers are anonymised so votes go to the argument, not the name. Ties break
deterministically to the lowest letter.

---

## Setup

You need **Python 3.10+** and a free OpenRouter API key.

1. **Get a free key** at <https://openrouter.ai/keys> (sign-up is free).

2. **Install and configure:**
   ```bash
   pip install -r requirements.txt
   cp .env.example .env
   # open .env and paste your key after OPENROUTER_API_KEY=
   ```

3. **Run it:**
   ```bash
   uvicorn app.main:app --reload
   ```

4. Open <http://localhost:8000>, pick a few models for each chamber, type a
   question, and click **Convene the Council**. Watch the proposals rise, the
   debate unfold, the judges cast their ostraka, and a winner get crowned.

> **Free-tier note:** free models have per-minute and daily rate limits. The app
> caps how many models it calls at once and retries when it's throttled, so
> keeping each chamber to a handful of models keeps things smooth. You can tune
> `OPENROUTER_MAX_CONCURRENCY` and `OPENROUTER_TIMEOUT` in `.env`.

---

## Project layout

```
app/
  main.py        FastAPI app: serves the page, lists models, streams the council (SSE)
  council.py     The two-chamber process: propose → debate → vote → ratify
  openrouter.py  Async OpenRouter client (retry/backoff + concurrency cap + model catalog)
  prompts.py     Prompt templates for each round
  config.py      Loads settings from .env
static/
  index.html     The agora
  style.css       Athenian theme (light "marble", dark "torchlight") + animations
  app.js          Roster pickers + live streaming of the proceedings
tests/
  test_council.py  Offline tests of the orchestration (no API key, no network)
```

---

## Testing

The orchestration is covered by offline tests that use a stub in place of
OpenRouter, so they need **no API key and make no network calls**:

```bash
pytest
```

They verify that answers are anonymised, that only governing models vote, that
votes tally correctly, that a failing debater is dropped without crashing the
council, and that ties resolve deterministically.

---

## Notes

- The design is a self-hosted page (not a claude.ai Artifact) because the app
  needs to call OpenRouter, which a sandboxed Artifact can't do. The look still
  follows the same design fundamentals. Fonts are system fonts — no CDN, so it
  works fully offline once running.
- Ideas for later: multi-round debate, a "chief archon" that synthesises the top
  answers instead of a raw vote, and a saved history of past councils.
