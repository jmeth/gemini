"""
Voice-command matching — fuzzy word matching, no ML.

The browser's speech recogniser hands us a transcript; we score it against every
trigger phrase of every command and fire the best one if it clears a threshold.
Scoring leans on two cheap signals from difflib:

- coverage: the fraction of the trigger's words that turn up (fuzzily) somewhere
  in what was said. This is the "did they say this phrase" signal and tolerates
  filler words around it ("okay, grab the red block please").
- ratio: whole-phrase similarity, which keeps a short exact phrase from being
  beaten by a long sloppy one and absorbs small mishearings.
"""

from __future__ import annotations

import difflib
import re

WORD_MATCH = 0.82   # per-word similarity for a trigger word to count as "heard"
ACCEPT = 0.6        # overall score a command must reach to fire


def _norm(text: str) -> list[str]:
    return re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower()).split()


def _phrase_score(phrase_words: list[str], said_words: list[str]) -> float:
    if not phrase_words or not said_words:
        return 0.0

    ratio = difflib.SequenceMatcher(
        None, " ".join(phrase_words), " ".join(said_words)
    ).ratio()

    hits = sum(
        1
        for w in phrase_words
        if any(
            difflib.SequenceMatcher(None, w, s).ratio() >= WORD_MATCH
            for s in said_words
        )
    )
    coverage = hits / len(phrase_words)

    return max(coverage, 0.5 * coverage + 0.5 * ratio)


def match_command(
    transcript: str, commands: list[dict]
) -> tuple[dict | None, float, str | None]:
    """Returns (command | None, best_score, matched_trigger | None)."""
    said = _norm(transcript)
    best: dict | None = None
    best_score = 0.0
    best_trigger: str | None = None

    for cmd in commands:
        if not cmd.get("enabled", True):
            continue
        for trigger in cmd.get("triggers", []):
            score = _phrase_score(_norm(trigger), said)
            if score > best_score:
                best, best_score, best_trigger = cmd, score, trigger

    if best is not None and best_score >= ACCEPT:
        return best, round(best_score, 3), best_trigger
    return None, round(best_score, 3), best_trigger
