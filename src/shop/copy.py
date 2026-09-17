"""Product copy written by the local model.

The risk with generated copy is not that it reads badly -- it is that it makes
things up. A description that invents a material, a certification or a
guarantee is a returns problem at best and a legal one at worst. So the model
is given only what the supplier said, told not to add anything, and its output
is checked before it is used. Anything that fails the check falls back to the
supplier's own words.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from jarvis.backends import Backend, BackendError

log = logging.getLogger("shop.copy")

# Claims nobody should make on our behalf without evidence.
FORBIDDEN = re.compile(
    r"\b(fda[- ]approved|ce[- ]certified|clinically proven|doctor[- ]recommended|"
    r"guaranteed|lifetime warranty|cures?|treats?|100% (?:safe|effective)|"
    r"best in the world|number one|award[- ]winning|organic|hypoallergenic|"
    r"waterproof|dishwasher[- ]safe|bpa[- ]free)\b",
    re.IGNORECASE,
)
# Tags that would lie about provenance.
FORBIDDEN_TAGS = {"handmade", "organic", "vegan", "fair trade", "recycled"}

PROMPT = """\
You are writing a product listing for an online shop.

Use ONLY the facts below. Do not invent materials, dimensions, weights,
certifications, safety claims, guarantees, origins or awards. If a detail is
not given, leave it out. Do not mention price, stock or shipping.

Facts:
{facts}

House style: {voice}

Reply with JSON only, no other text, in exactly this shape:
{{"title": "...", "description": "...", "seo_title": "...",
  "seo_description": "...", "tags": ["...", "..."]}}

Rules:
- title: under 70 characters, plain, no ALL CAPS, no exclamation marks.
- description: two short paragraphs of plain text, no HTML, under 700
  characters, describing only what the facts support.
- seo_title: under 60 characters. seo_description: under 155 characters.
- tags: three to six lowercase words drawn from the facts."""


@dataclass
class Body:
    title: str
    html: str
    seo_title: str = ""
    seo_description: str = ""
    tags: list[str] = None

    def __post_init__(self):
        self.tags = self.tags or []


class Writer:
    """Generates listing copy, falling back to the supplier's text."""

    def __init__(self, backend: Backend, voice: str = "plain and specific"):
        self.backend = backend
        self.voice = voice
        self.generated = 0
        self.rejected = 0

    def describe(self, item) -> Body:
        facts = _facts(item)
        try:
            raw = self._ask(facts)
        except BackendError as exc:
            log.warning("copy for %s fell back: %s", item.sku, exc)
            return _fallback(item)

        body = _parse(raw)
        if body is None:
            self.rejected += 1
            log.info("copy for %s was not usable JSON", item.sku)
            return _fallback(item)

        problem = _check(body, item)
        if problem:
            self.rejected += 1
            log.info("copy for %s rejected: %s", item.sku, problem)
            return _fallback(item)

        self.generated += 1
        return body

    def _ask(self, facts: str) -> str:
        messages = [
            {
                "role": "system",
                "content": "You write accurate, plain product copy. JSON only.",
            },
            {
                "role": "user",
                "content": PROMPT.format(facts=facts, voice=self.voice),
            },
        ]
        completion = self.backend.chat(messages, [])
        return completion.text


def _facts(item) -> str:
    lines = [f"- Product name: {item.title}", f"- SKU: {item.sku}"]
    if item.vendor:
        lines.append(f"- Brand: {item.vendor}")
    if item.product_type:
        lines.append(f"- Category: {item.product_type}")
    if item.description:
        lines.append(f"- Supplier's description: {item.description}")
    if item.weight:
        lines.append(f"- Weight: {item.weight}")
    for key, value in (item.extra or {}).items():
        lines.append(f"- {key}: {value}")
    return "\n".join(lines)


def _parse(raw: str) -> Body | None:
    """Pull the JSON object out of a reply that may be wrapped in chatter."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?|```$", "", text, flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not data.get("title"):
        return None
    description = str(data.get("description") or "").strip()
    tags = [str(t).strip().lower() for t in (data.get("tags") or []) if str(t).strip()]
    return Body(
        title=str(data["title"]).strip(),
        html=_to_html(description),
        seo_title=str(data.get("seo_title") or "").strip()[:60],
        seo_description=str(data.get("seo_description") or "").strip()[:155],
        tags=tags[:6],
    )


def _check(body: Body, item) -> str:
    """Reject copy that is too long, shouty, or making things up."""
    if len(body.title) > 90:
        return "title too long"
    if body.title.isupper():
        return "title is shouting"
    text = f"{body.title} {body.html}"
    match = FORBIDDEN.search(text)
    if match and match.group(0).lower() not in _source_text(item):
        return f"unsupported claim: {match.group(0)!r}"
    for tag in body.tags:
        if tag in FORBIDDEN_TAGS and tag not in _source_text(item):
            return f"unsupported tag: {tag!r}"
    if len(body.html) > 2000:
        return "description too long"
    if not body.html.strip():
        return "empty description"
    return ""


def _source_text(item) -> str:
    return " ".join(
        str(part).lower()
        for part in (item.title, item.description, item.product_type, item.vendor)
        if part
    )


def _to_html(text: str) -> str:
    import html as html_module

    paragraphs = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
    return "".join(
        f"<p>{html_module.escape(p, quote=False)}</p>" for p in paragraphs
    )


def _fallback(item) -> Body:
    from .catalog import _plain_description

    return Body(
        title=item.title[:255],
        html=_plain_description(item),
        seo_title=item.title[:60],
        seo_description=(item.description or item.title)[:155],
        tags=item.tags[:6],
    )
