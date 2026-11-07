"""Versioned codec for catalogue-walk checkpoints (shared by the collectors).

A catalogue checkpoint is *traversal progress*, never evidence about
availability: it records where a bounded walk stopped so the next run resumes
there instead of re-walking from the top. It is stored as opaque text in
``availability.SyncCursor.cursor`` — the row is unique on
``(provider, source, country)``, so the value must carry the progress of every
*part* of a multi-part walk (CBC's three category indices, Pluto's two hubs),
not just one offset.

The wire format is deliberately tiny and boring::

    {"v": 1, "parts": {"shows": "murdoch-mysteries"}, "done": ["all-films"]}

``v`` is the algorithm version. A walk that changes *how* it traverses must
bump it, which turns the change into a controlled, tested reset instead of a
resume from a stale anchor.

Each part of the walk is in exactly one of three states, and the distinction
between the first two is load-bearing:

- **not started** — absent from ``parts`` and from ``done``. Start at its head.
- **complete** — listed in ``done``. Skip it entirely: every entry was already
  walked. This must be representable *separately* from an anchor, because
  "finished" and "start at the head" are opposite instructions. Collapsing them
  (e.g. clearing a finished part's anchor to ``""``) makes the next run re-walk
  that part, and a walk that re-walks a part it already finished can oscillate
  forever without ever advancing — which is exactly the bug this module's
  explicit ``done`` list exists to prevent.
- **in progress** — has an anchor in ``parts``. Resume *after* that identity.

Two more properties matter more than the encoding:

1. **Bounded.** ``SyncCursor.cursor`` is an unbounded ``TextField``, but a
   checkpoint is a *pointer*, not a record set. ``encode`` refuses to emit more
   than :data:`MAX_PARTS` entries per list and clips each identity, so a runaway
   walk can never grow the row without bound.
2. **Fail-safe.** Every unreadable input — malformed JSON, a foreign cursor
   written by another source, a version bump, an oversized or wrongly-shaped
   payload — decodes to "start every part at its head". A broken checkpoint
   costs repeated work; it must never cost a *skipped* entry, and it must never
   be mistaken for a completed walk. Callers log the reason and carry on.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Iterable

logger = logging.getLogger(__name__)

#: Highest number of entries either list may carry. Real walks use a handful
#: (CBC: 3, Pluto: 2); the cap exists so a corrupted or hostile value cannot be
#: decoded into an unbounded structure.
MAX_PARTS = 256

#: Longest identity (slug / URL) stored per part. Provider identities are far
#: shorter; clipping is a corruption guard, not a truncation of real data.
MAX_IDENTITY_CHARS = 512


@dataclass(frozen=True)
class ResumeState:
    """Where a previous walk stopped, per part. Persistence-free."""

    #: part -> last identity the walk completed inside it (resume *after* this).
    anchors: dict = field(default_factory=dict)
    #: parts the walk finished; they are skipped entirely.
    done: frozenset = frozenset()

    def is_done(self, part: str) -> bool:
        return part in self.done

    def anchor(self, part: str) -> str:
        """The identity to resume after in *part*, or ``""`` for its head."""
        return str(self.anchors.get(part) or "")


def encode(version: int, anchors: dict, done: Iterable[str] = ()) -> str:
    """Serialise walk progress into one cursor string.

    Empty anchors are dropped: a part with no anchor is either not started or
    finished, and both are expressed without a value. A part listed in *done* is
    never also stored as an anchor — completion wins, so the two can never
    contradict each other in the stored value.
    """
    finished = {str(part) for part in done if part}
    payload = {
        "v": int(version),
        "parts": {
            str(key): str(value)[:MAX_IDENTITY_CHARS]
            for key, value in sorted(anchors.items())
            if value and str(key) not in finished
        },
    }
    if finished:
        payload["done"] = sorted(finished)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def decode(cursor, *, version: int, parts, log_prefix: str) -> ResumeState:
    """Decode a stored checkpoint, tolerating old, foreign or garbage values.

    Anything unreadable yields an empty :class:`ResumeState` — every declared
    part starts at its head — so the caller restarts rather than resuming
    garbage or, worse, looking finished.
    """
    declared = {str(part) for part in parts}
    empty = ResumeState(anchors={part: "" for part in declared})
    if cursor in (None, ""):
        return empty

    data = cursor
    if not isinstance(cursor, dict):
        try:
            data = json.loads(str(cursor))
        except (TypeError, ValueError):
            logger.warning(
                "%s_cursor_invalid cursor=%r", log_prefix, str(cursor)[:160]
            )
            return empty

    if not isinstance(data, dict) or data.get("v") != version:
        logger.warning(
            "%s_cursor_reset version=%r",
            log_prefix,
            data.get("v") if isinstance(data, dict) else None,
        )
        return empty

    stored = data.get("parts")
    raw_done = data.get("done", [])
    if (
        not isinstance(stored, dict)
        or len(stored) > MAX_PARTS
        or not isinstance(raw_done, list)
        or len(raw_done) > MAX_PARTS
    ):
        logger.warning(
            "%s_cursor_reset reason=shape parts=%s done=%s",
            log_prefix, type(stored).__name__, type(raw_done).__name__,
        )
        return empty

    # Unknown part names are ignored rather than trusted: a cursor written by an
    # older/newer part list must not inject progress for a part this walk does
    # not have.
    anchors = {part: "" for part in declared}
    for key, value in stored.items():
        if key in declared and isinstance(value, str) and value:
            anchors[key] = value
    done = frozenset(
        part for part in raw_done
        if isinstance(part, str) and part in declared
    )
    # Completion wins over a stale anchor for the same part.
    for part in done:
        anchors[part] = ""
    return ResumeState(anchors=anchors, done=done)
