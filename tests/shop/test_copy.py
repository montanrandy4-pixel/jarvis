"""Generated product copy, and the checks that stop it inventing things."""

from __future__ import annotations

import json

import pytest

from jarvis.backends import BackendError, Completion
from shop.copy import Writer, _check, _parse
from shop.feed import FeedItem


class ScriptedBackend:
    name = "scripted"
    model = "test"
    description = "scripted (test)"

    def __init__(self, reply="", error=None):
        self.reply = reply
        self.error = error
        self.prompts = []

    def chat(self, messages, tools, *, on_text=None, cancel=None):
        self.prompts.append(messages[-1]["content"])
        if self.error:
            raise self.error
        return Completion(text=self.reply)

    def health(self):
        return True, "scripted"


ITEM = FeedItem(
    sku="A-1",
    title="Brass Desk Lamp",
    cost=24.5,
    description="An adjustable brass lamp with a linen shade.",
    vendor="Fenwick",
)


def reply(**fields) -> str:
    body = {
        "title": "Brass Desk Lamp",
        "description": "A warm little lamp.\n\nIt adjusts.",
        "seo_title": "Brass Desk Lamp",
        "seo_description": "A warm brass lamp with a linen shade.",
        "tags": ["lamp", "brass"],
    }
    body.update(fields)
    return json.dumps(body)


class TestParsing:
    def test_json_wrapped_in_a_code_fence_is_read(self):
        body = _parse(f"```json\n{reply()}\n```")

        assert body.title == "Brass Desk Lamp"
        assert body.html == "<p>A warm little lamp.</p><p>It adjusts.</p>"

    def test_json_with_chatter_around_it_is_read(self):
        assert _parse(f"Sure! Here you go:\n{reply()}\nHope that helps.") is not None

    def test_text_with_no_json_is_rejected(self):
        assert _parse("I'm afraid I can't do that.") is None

    def test_description_is_escaped_into_html(self):
        body = _parse(reply(description="Bolts & nuts <script>alert(1)</script>"))

        assert "&amp;" in body.html and "<script>" not in body.html


class TestClaimChecking:
    @pytest.mark.parametrize(
        "claim",
        ["FDA-approved and safe", "Guaranteed to last", "Award-winning design",
         "100% safe for children", "Dishwasher safe"],
    )
    def test_claims_the_supplier_never_made_are_rejected(self, claim):
        body = _parse(reply(description=claim))

        assert "unsupported claim" in _check(body, ITEM)

    def test_a_claim_the_supplier_did_make_is_allowed(self):
        item = FeedItem(sku="B", title="Mug", cost=1.0,
                        description="Dishwasher safe stoneware.")
        body = _parse(reply(description="Dishwasher safe, and pleasant to hold."))

        assert _check(body, item) == ""

    def test_tags_that_would_lie_about_provenance_are_rejected(self):
        body = _parse(reply(tags=["organic", "lamp"]))

        assert "unsupported tag" in _check(body, ITEM)

    def test_a_shouting_title_is_rejected(self):
        assert "shouting" in _check(_parse(reply(title="BUY NOW!!!")), ITEM)

    def test_an_overlong_title_is_rejected(self):
        assert "too long" in _check(_parse(reply(title="x" * 120)), ITEM)


class TestWriter:
    def test_good_copy_is_used(self):
        writer = Writer(ScriptedBackend(reply()))

        body = writer.describe(ITEM)

        assert body.title == "Brass Desk Lamp"
        assert writer.generated == 1 and writer.rejected == 0

    def test_only_the_supplied_facts_are_put_in_the_prompt(self):
        backend = ScriptedBackend(reply())

        Writer(backend).describe(ITEM)
        prompt = backend.prompts[0]

        assert "Brass Desk Lamp" in prompt and "Fenwick" in prompt
        assert "Do not invent" in prompt
        assert "24.5" not in prompt  # cost is never shown to the copywriter

    def test_rejected_copy_falls_back_to_the_suppliers_words(self):
        writer = Writer(ScriptedBackend(reply(description="Guaranteed forever")))

        body = writer.describe(ITEM)

        assert "adjustable brass lamp" in body.html
        assert writer.rejected == 1

    def test_an_unusable_reply_falls_back(self):
        writer = Writer(ScriptedBackend("no json here"))

        assert "adjustable brass lamp" in writer.describe(ITEM).html

    def test_a_model_failure_falls_back_rather_than_stopping_the_sync(self):
        writer = Writer(ScriptedBackend(error=BackendError("ollama is not running")))

        body = writer.describe(ITEM)

        assert body.title == "Brass Desk Lamp"
        assert body.html  # the listing still has a description
