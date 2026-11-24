"""Product tags for a company, extracted from its own description.

The regex prototype that preceded this was wrong about one company in three --
it offered "Incorporated in 1999" and "Leading end-to-end" as products. A tag
sitting beside a company name reads as a fact about the business, and a reader
cannot tell a parsing artefact from a real one, so that bar was too low to ship.

A model reads the sentence instead. Three things keep that honest:

* It runs **once per description**, offline, and the result is stored. Nothing
  is extracted at render time, so a page view costs nothing and two readers of
  the same company always see the same tags.
* Output is constrained to a schema, so a malformed reply is a failed request
  rather than a company tagged with a paragraph.
* The tags are keyed to the text they came from. If a re-scrape changes the
  description, the old tags no longer match and the company is offered again.

Pure except for :func:`extract`. Everything that decides *what to ask* and
*what to accept* is a plain function, so the parts that can be wrong without
anyone noticing are the parts that are tested.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

log = logging.getLogger(__name__)

#: Opus, deliberately, for a job that runs once per company and is then frozen.
#: The failure this replaces was a cheap extractor being wrong often enough to
#: be unshippable; spending less per call to land back there would be a saving
#: of the wrong thing. At roughly 300 input and 40 output tokens per company,
#: the whole universe is a few dollars through the batch endpoint.
MODEL = "claude-opus-4-8"

#: Few enough to read at a glance on a card. A company that genuinely does five
#: things is better served by the description than by five chips.
MAX_TAGS = 3
#: Long enough for "Speciality chemicals", short enough not to wrap a chip.
MAX_TAG_CHARS = 28

SYSTEM = """\
You label listed Indian companies with the products or services they sell, for \
a stock screener that shows the labels as small chips beside the company name.

Rules, in order of importance:

1. Only use what the description states. If it does not name a product or \
service, return no tags. Never infer from the company name, the industry it \
sounds like, or anything you know about the company from elsewhere. A missing \
tag costs a reader nothing; a wrong one is a false claim about a business.
2. Tag the thing sold, not the company's description of itself. "Leading \
end-to-end solutions provider" is not a product. "Printed circuit boards" is.
3. Use the words the description uses, lightly normalised: singular to plural \
where natural, title case, no trailing punctuation.
4. At most {max_tags} tags, each at most {max_chars} characters.
5. Never return a year, a company name, a brand name, a place, or a phrase \
beginning with Leading, Largest, One of, Incorporated, Established, or Part of.

Examples:

Description: "Selling of fashion casual garments and accessories under the \
brand name MUFTI"
Tags: ["Casual garments", "Accessories"]

Description: "Designs and manufactures wireline and wireless networking \
products"
Tags: ["Wireline networking", "Wireless networking"]

Description: "One of India's leading high-end computing solutions providers"
Tags: ["Computing solutions"]

Description: "A global enterprise providing end-to-end solutions to utilities \
and industries for the management of electrical energy"
Tags: []
"""

#: Constrains the reply so a malformed answer is a failed request rather than a
#: company labelled with a sentence.
SCHEMA = {
    "type": "object",
    "properties": {
        "tags": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["tags"],
    "additionalProperties": False,
}

#: Openers that describe a company's self-image rather than its products. The
#: prompt forbids these; this rejects them anyway, because a prompt is a request
#: and a reader cannot tell which tags were checked.
_BANNED_OPENERS = (
    "leading", "largest", "one of", "incorporated", "established", "founded",
    "part of", "flagship", "india's", "indias", "world's", "worlds", "a ", "an ",
    "the ",
)


@dataclass(frozen=True)
class Tags:
    """Tags, and the text they were read from."""

    tags: tuple[str, ...]
    #: Digest of the description. A re-scrape that rewrites the description
    #: changes this, which is what makes the tags visibly stale rather than
    #: quietly wrong.
    source_sha: str

    def as_json(self) -> str:
        return json.dumps({"source_sha": self.source_sha, "tags": list(self.tags)},
                          separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str | None) -> "Tags | None":
        if not raw:
            return None
        try:
            payload = json.loads(raw)
            return cls(tuple(payload["tags"]), payload["source_sha"])
        except (ValueError, KeyError, TypeError):
            # A row written by an older shape, or corrupted. Treat as untagged
            # rather than crashing a page that is mostly about something else.
            log.warning("unreadable tags payload; treating as absent")
            return None

    def describes(self, about: str | None) -> bool:
        """Whether these tags were read from this exact description."""
        return about is not None and digest(about) == self.source_sha


def digest(about: str) -> str:
    """Stable digest of a description, whitespace-insensitive."""
    return hashlib.sha256(" ".join(about.split()).encode()).hexdigest()[:16]


def clean(raw: Iterable[Any]) -> tuple[str, ...]:
    """Accept only tags that could sit beside a company name as a fact.

    Applied to the model's reply, not instead of asking well. The prompt states
    these rules and the model follows them nearly always -- but "nearly always"
    across 2,700 companies is tens of bad chips, and the reader has no way to
    tell a checked tag from an unchecked one.
    """
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            continue
        tag = " ".join(item.split()).strip(" .,;:-")
        if not tag or len(tag) > MAX_TAG_CHARS or len(tag) < 3:
            continue
        lowered = tag.lower()
        if lowered.startswith(_BANNED_OPENERS):
            continue
        if any(ch.isdigit() for ch in tag):
            continue
        if lowered in seen:
            continue
        seen.add(lowered)
        out.append(tag[0].upper() + tag[1:])
        if len(out) == MAX_TAGS:
            break
    return tuple(out)


def request_for(ticker: str, about: str) -> dict:
    """One entry in a batch, keyed so the reply can be matched back.

    Results come back in arbitrary order, so the ticker travels as `custom_id`
    rather than being inferred from position.
    """
    return {
        "custom_id": ticker,
        "params": {
            "model": MODEL,
            "max_tokens": 256,
            "system": SYSTEM.format(max_tags=MAX_TAGS, max_chars=MAX_TAG_CHARS),
            "output_config": {
                "format": {"type": "json_schema", "schema": SCHEMA},
            },
            "messages": [{"role": "user", "content": f"Description: {about!r}"}],
        },
    }


def tags_from_reply(text: str, about: str) -> Tags:
    """Read a model reply into tags, keyed to the description it came from.

    A reply that is not the shape asked for yields no tags rather than raising:
    one unreadable company must not fail a batch of two thousand.
    """
    try:
        payload = json.loads(text)
        raw = payload.get("tags", [])
    except (ValueError, AttributeError):
        log.warning("reply was not the requested JSON; no tags taken")
        raw = []
    if not isinstance(raw, list):
        raw = []
    return Tags(clean(raw), digest(about))


def needs_tagging(about: str | None, stored: str | None) -> bool:
    """Whether a company should be offered to the next run.

    Three states mean yes: never tagged, tagged from a description that has
    since changed, and a stored payload that cannot be read. A company whose
    description genuinely yields no tags is *not* offered again -- an empty
    result is an answer, and re-asking it every night would spend the whole
    budget on companies that have already been judged.
    """
    if not about or not about.strip():
        return False
    existing = Tags.from_json(stored)
    return existing is None or not existing.describes(about)


# --------------------------------------------------------------------- batch

def untagged(store, limit: int | None = None) -> list[tuple[str, str]]:
    """(ticker, description) for companies needing tags, ticker order.

    One query rather than a round trip per company: this picks a batch out of
    ~2,700 against a hosted database, and asking individually would be 2,700
    round trips before a single request is built.
    """
    rows = store.conn.execute(
        "SELECT ticker, about, tags FROM companies "
        "WHERE scan_for_results = 'Y' AND about IS NOT NULL ORDER BY ticker"
    )
    out = [(r["ticker"], r["about"]) for r in rows
           if needs_tagging(r["about"], r["tags"])]
    return out[:limit] if limit else out


def extract(client, pairs: Sequence[tuple[str, str]], *, poll: int = 30,
            timeout: int = 60 * 60) -> dict[str, Tags]:
    """Tag a batch of companies, blocking until the batch ends.

    The Batches endpoint rather than a request each: this is offline work with
    no reader waiting, it is half the price, and it removes the concurrency and
    rate-limit handling that 2,700 individual calls would otherwise need.

    Results are keyed by `custom_id` and arrive in arbitrary order -- matching
    by position would file one company's products under another, which nothing
    downstream could detect.

    A company whose request failed is simply absent from the result, so it is
    offered again next run rather than being stored as "no tags".
    """
    import time

    if not pairs:
        return {}
    by_ticker = dict(pairs)
    batch = client.messages.batches.create(
        requests=[request_for(t, a) for t, a in pairs])
    log.info("submitted %d companies as batch %s", len(pairs), batch.id)

    deadline = time.monotonic() + timeout
    while True:
        batch = client.messages.batches.retrieve(batch.id)
        if batch.processing_status == "ended":
            break
        if time.monotonic() > deadline:
            raise TimeoutError(
                f"batch {batch.id} still {batch.processing_status} after "
                f"{timeout}s; results stay retrievable for 29 days")
        log.debug("batch %s: %s", batch.id, batch.processing_status)
        time.sleep(poll)

    out: dict[str, Tags] = {}
    failed = 0
    for result in client.messages.batches.results(batch.id):
        ticker = result.custom_id
        if result.result.type != "succeeded":
            failed += 1
            log.warning("%s: %s", ticker, result.result.type)
            continue
        text = "".join(b.text for b in result.result.message.content
                       if getattr(b, "type", None) == "text")
        out[ticker] = tags_from_reply(text, by_ticker[ticker])
    if failed:
        log.warning("%d of %d failed; they will be offered again", failed, len(pairs))
    return out
