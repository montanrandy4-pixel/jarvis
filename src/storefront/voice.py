"""Answering the phone.

When you ring the shop's number, Twilio posts to a webhook and expects TwiML
back. This turns the question you asked into an answer drawn from the same
state the workspace shows, and speaks it.

Three decisions worth knowing:

* **Answers are deterministic, not generated.** A phone call is a bad place
  for a language model to improvise about money. Intents are matched against
  the question and answered from real numbers; anything unrecognised says so
  and lists what it can answer, rather than guessing plausibly.
* **Requests are verified.** The webhook is on a public URL, and without a
  signature check anyone who found it could ring up and be read your revenue.
  Twilio signs every request; unsigned or wrongly signed ones are refused.
* **It is read-only.** You can ask the agent anything about the shop. You
  cannot tell it to change the shop, over a channel authenticated by nothing
  but possession of a phone number.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
from dataclasses import dataclass
from xml.sax.saxutils import escape

log = logging.getLogger("storefront.voice")

GREETING = "Shop agent here. What would you like to know?"
UNSURE = (
    "Sorry, I did not catch that. You can ask about revenue, orders, "
    "problems, or the overall status."
)


def verify(signature: str, url: str, params: dict, auth_token: str) -> bool:
    """Twilio's request signature: HMAC-SHA1 over the URL and sorted params.

    Returns False on anything missing, so an unconfigured token fails closed
    rather than letting every caller through.
    """
    if not signature or not auth_token or not url:
        return False
    payload = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    digest = hmac.new(
        auth_token.encode("utf-8"), payload.encode("utf-8"), hashlib.sha1
    ).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode("ascii"), signature)


def twiml(*parts: str) -> str:
    return '<?xml version="1.0" encoding="UTF-8"?><Response>' + "".join(parts) + "</Response>"


def say(text: str) -> str:
    return f'<Say voice="Polly.Joanna">{escape(text)}</Say>'


def gather(prompt: str, action: str = "/voice/answer") -> str:
    """Listen for a spoken question, then post it to `action`."""
    return (
        f'<Gather input="speech" action="{escape(action)}" method="POST" '
        f'speechTimeout="auto" language="en-US">{say(prompt)}</Gather>'
        + say("I did not hear anything. Goodbye.")
    )


# --------------------------------------------------------------------------
# Intents. Ordered: the first that matches wins, so put the specific first.
# --------------------------------------------------------------------------
INTENTS: list[tuple[str, re.Pattern]] = [
    ("problems", re.compile(
        r"\b(problem|wrong|broken|blocker|issue|fail|error|attention|fix)\w*", re.I)),
    ("revenue", re.compile(
        r"\b(revenue|sales|money|earn|made|takings?|income|sold)\w*", re.I)),
    ("orders", re.compile(r"\b(order|purchase|customer|buyer|sale)\w*", re.I)),
    ("products", re.compile(r"\b(product|catalog(?:ue)?|listing|item)\w*", re.I)),
    ("alerts", re.compile(r"\b(alert|notification|warning|told me)\w*", re.I)),
    ("status", re.compile(
        r"\b(status|how('?s| is| are)|everything|ok|okay|alright|going|update)\w*", re.I)),
]


def classify(question: str) -> str:
    for name, pattern in INTENTS:
        if pattern.search(question or ""):
            return name
    return "unknown"


def _money(summary: dict) -> str:
    amount = summary.get("revenue")
    if amount is None:
        return "no revenue figure yet"
    currency = summary.get("currency", "")
    spoken = "dollars" if currency == "USD" else currency
    return f"{amount:,.2f} {spoken}".strip()


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def answer(question: str, snapshot: dict) -> str:
    """The spoken reply. Always a real number or an honest 'I do not know'."""
    intent = classify(question)
    products = snapshot.get("products") or []
    summary = snapshot.get("summary") or {}
    alerts = snapshot.get("alerts") or []
    blockers = [p for p in products if p.get("state") == "blocker"]
    warnings = [p for p in products if p.get("state") == "warning"]

    if intent == "revenue":
        orders = summary.get("orders", 0)
        if not orders:
            return "No orders yet, so no revenue to report."
        return (
            f"{_money(summary)} across {_plural(orders, 'order')}, "
            f"averaging {summary.get('average', 0):,.2f}."
        )

    if intent == "orders":
        orders = summary.get("orders", 0)
        if not orders:
            return "No orders in the current window."
        flagged = summary.get("needs_review", 0)
        reply = _plural(orders, "order") + "."
        if flagged == 1:
            reply += " One of them needs review."
        elif flagged:
            reply += f" {flagged} of them need review."
        else:
            reply += " Nothing needs review."
        return reply

    if intent == "problems":
        if not products:
            return "I have not checked the catalogue yet."
        if not blockers and not warnings:
            return "Nothing is wrong. Every product is deliverable."
        parts = []
        if blockers:
            parts.append(
                f"{_plural(len(blockers), 'product')} cannot be delivered: "
                + ", ".join(p["title"] for p in blockers[:3])
                + ("," if len(blockers) > 3 else "")
                + (f" and {len(blockers) - 3} more." if len(blockers) > 3 else ".")
            )
        if warnings:
            parts.append(f"{_plural(len(warnings), 'product')} needs attention.")
        return " ".join(parts)

    if intent == "products":
        if not products:
            return "I have not checked the catalogue yet."
        return (
            f"{_plural(len(products), 'product')} in the catalogue. "
            f"{len(products) - len(blockers)} deliverable, {len(blockers)} blocked."
        )

    if intent == "alerts":
        if not alerts:
            return "No alerts have fired."
        recent = alerts[-1]
        return (
            f"{_plural(len(alerts), 'alert')} so far. The most recent: "
            f"{recent.get('title', 'unknown')}."
        )

    if intent == "status":
        if not products:
            return "The agent is running but has not completed a check yet."
        if blockers:
            return (
                f"Not good. {_plural(len(blockers), 'product')} cannot be "
                f"delivered to a buyer. Revenue is {_money(summary)}."
            )
        return (
            f"All good. {_plural(len(products), 'product')} deliverable, "
            f"revenue {_money(summary)}."
        )

    return UNSURE


def incoming_call() -> str:
    return twiml(gather(GREETING))


def spoken_answer(question: str, snapshot: dict) -> str:
    """Answer, then listen again -- so a call can be a conversation."""
    reply = answer(question, snapshot)
    return twiml(
        say(reply),
        gather("Anything else?"),
    )


def rejected() -> str:
    """Wrong or missing signature. Say nothing about the shop."""
    return twiml(say("Sorry, this request could not be verified. Goodbye."))
