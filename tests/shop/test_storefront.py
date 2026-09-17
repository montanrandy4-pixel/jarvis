"""Building the store's structure from store.toml."""

from __future__ import annotations

import pytest
from fake_shopify import TOKEN

from shop import storefront
from shop.client import ShopifyClient
from shop.scaffold import STORE_TOML
from shop.storefront import markdown_to_html
from shop.store import StoreSpec

import tomllib


@pytest.fixture
def spec():
    return StoreSpec.from_dict(tomllib.loads(STORE_TOML))


@pytest.fixture
def live(api):
    return ShopifyClient(api.endpoint, TOKEN, dry_run=False)


def build(spec, client):
    plan = storefront.plan(spec, client)
    return plan, storefront.apply(plan, client)


class TestMarkdown:
    def test_headings_and_paragraphs(self):
        assert markdown_to_html("## Title\n\nA line.") == "<h2>Title</h2><p>A line.</p>"

    def test_bold_and_italic(self):
        assert markdown_to_html("**bold** and *thin*") == (
            "<p><strong>bold</strong> and <em>thin</em></p>"
        )

    def test_tables_become_tables(self):
        html = markdown_to_html("| A | B |\n|---|---|\n| 1 | 2 |")

        assert "<table>" in html and "<th>A</th>" in html and "<td>2</td>" in html

    def test_html_in_the_source_is_escaped(self):
        assert "<script>" not in markdown_to_html("<script>alert(1)</script>")

    def test_wrapped_lines_join_into_one_paragraph(self):
        assert markdown_to_html("one\ntwo") == "<p>one two</p>"


class TestBuilding:
    def test_an_empty_store_gets_the_whole_structure(self, spec, live, api):
        plan, done = build(spec, live)

        assert done["failed"] == 0
        assert len(api.collections) == 4
        assert len(api.pages) == 3
        assert len(api.policies) == 4
        assert set(api.menus) == {"main-menu", "footer"}

    def test_collections_with_a_tag_build_themselves(self, spec, live, api):
        build(spec, live)

        lighting = api.collections["lighting"]
        rules = lighting["ruleSet"]["rules"]
        assert rules == [{"column": "TAG", "relation": "EQUALS",
                          "condition": "lighting"}]

    def test_policies_are_mapped_to_shopifys_own_types(self, spec, live, api):
        build(spec, live)

        assert set(api.policies) == {
            "REFUND_POLICY", "PRIVACY_POLICY", "TERMS_OF_SERVICE", "SHIPPING_POLICY"
        }
        assert "<h2>Returns and refunds</h2>" in api.policies["REFUND_POLICY"]["body"]

    def test_menus_carry_their_items(self, spec, live, api):
        build(spec, live)

        titles = [item["title"] for item in api.menus["main-menu"]["items"]]
        assert titles == ["Lighting", "Shelving & Storage", "Kitchen & Table", "About"]

    def test_building_twice_writes_nothing_the_second_time(self, spec, live, api):
        build(spec, live)
        api.calls.clear()

        plan, done = build(spec, live)

        assert done == {"created": 0, "updated": 0, "failed": 0}
        assert plan.writes == []
        assert not [c for c in api.calls if "Create" in c or "Update" in c]

    def test_only_the_edited_page_is_rewritten(self, spec, live, api):
        build(spec, live)
        spec.pages[0].body = "We have moved to a bigger workshop."

        plan, done = build(spec, live)

        assert done == {"created": 0, "updated": 1, "failed": 0}
        changed = [s for s in plan.writes]
        assert len(changed) == 1 and changed[0].handle == "about"

    def test_a_renamed_collection_is_updated_not_duplicated(self, spec, live, api):
        build(spec, live)
        spec.collections[0].title = "Lamps"

        _, done = build(spec, live)

        assert done["updated"] == 1
        assert len(api.collections) == 4
        assert api.collections["lighting"]["title"] == "Lamps"

    def test_one_failure_does_not_stop_the_rest(self, spec, live, api):
        # Something else already owns the handle.
        api.collections["lighting"] = {"id": "x", "handle": "lighting",
                                       "title": "Theirs", "descriptionHtml": ""}
        api.collections["lighting"]["id"] = "gid://shopify/Collection/999"

        plan, done = build(spec, live)

        # It updates the existing one rather than failing, and the rest proceed.
        assert done["failed"] == 0
        assert len(api.pages) == 3

    def test_a_dry_run_plans_without_writing(self, spec, api):
        dry = ShopifyClient(api.endpoint, TOKEN, dry_run=True)

        plan = storefront.plan(spec, dry)
        storefront.apply(plan, dry)

        assert len(plan.writes) == 13
        assert api.collections == {} and api.pages == {} and api.policies == {}


class TestSpec:
    def test_the_scaffolded_store_is_complete(self, spec):
        assert spec.problems() == []

    def test_a_bare_store_lists_what_it_is_missing(self):
        problems = StoreSpec().problems()

        assert any("brand.name" in p for p in problems)
        assert any("refund" in p for p in problems)
        assert any("privacy" in p for p in problems)

    def test_handles_are_derived_from_titles(self):
        spec = StoreSpec.from_dict(
            {"collections": [{"title": "Kitchen & Table"}],
             "pages": [{"title": "About Us"}]}
        )

        assert spec.collections[0].handle == "kitchen-table"
        assert spec.pages[0].handle == "about-us"

    def test_duplicate_handles_are_reported(self):
        spec = StoreSpec.from_dict(
            {"brand": {"name": "X", "email": "a@b.c"},
             "collections": [{"title": "Lamps"}, {"title": "Lamps"}],
             "policies": {"refund": "x", "privacy": "x", "terms": "x",
                          "shipping": "x"}}
        )

        assert "two collections share a handle" in spec.problems()
