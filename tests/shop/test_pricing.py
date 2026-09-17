"""Turning cost into price."""

from __future__ import annotations

from decimal import Decimal

import pytest

from shop.config import PricingRules
from shop.pricing import apply_charm, price_for, worth_changing


class TestCharmEndings:
    @pytest.mark.parametrize(
        "amount, ending, expected",
        [
            ("61.25", "0.99", "61.99"), ("61.99", "0.99", "61.99"),
            ("62.00", "0.99", "62.99"), ("61.25", "0.95", "61.95"),
            ("61.96", "0.95", "62.95"), ("61.25", "5", "65.00"),
            ("61.25", "none", "61.25"), ("61.25", "", "61.25"),
        ],
    )
    def test_rounding_lands_on_the_intended_ending(self, amount, ending, expected):
        assert apply_charm(Decimal(amount), ending) == Decimal(expected)


class TestPrices:
    def test_markup_is_applied_then_charmed(self):
        price = price_for(24.50, PricingRules(markup=2.5, charm_ending="0.99"))

        assert price.as_strings() == ("61.99", None)
        assert not price.held

    def test_handling_is_added_before_rounding(self):
        rules = PricingRules(markup=2.0, handling=5.0, charm_ending="0.99")

        assert price_for(10.0, rules).as_strings()[0] == "25.99"

    def test_a_floor_lifts_cheap_items(self):
        rules = PricingRules(markup=1.2, floor=15.0, charm_ending="0.99")

        assert price_for(1.0, rules).as_strings()[0] == "15.99"

    def test_a_ceiling_holds_expensive_items_for_review(self):
        price = price_for(500.0, PricingRules(markup=2.5, ceiling=100.0))

        assert price.held and "above the ceiling" in price.hold

    def test_a_price_below_cost_is_always_held(self):
        price = price_for(100.0, PricingRules(markup=0.5))

        assert price.held and "would not cover the cost" in price.hold

    def test_the_suppliers_rrp_becomes_an_honest_compare_at_price(self):
        price = price_for(24.50, PricingRules(markup=2.5), suggested=99.0)

        assert price.as_strings() == ("61.99", "99.00")

    def test_a_multiplier_can_set_the_compare_at_price_instead(self):
        rules = PricingRules(markup=2.5, compare_at_multiplier=1.5)

        assert price_for(24.50, rules).as_strings()[1] == "92.99"

    def test_an_rrp_below_our_price_is_not_used(self):
        # Pretending something was cheaper than it is would be a lie.
        price = price_for(24.50, PricingRules(markup=2.5), suggested=30.0)

        assert price.as_strings()[1] is None

    def test_the_same_cost_always_gives_the_same_price(self):
        rules = PricingRules(markup=2.5)

        assert price_for(24.50, rules).amount == price_for(24.50, rules).amount


class TestChangeThreshold:
    def test_an_unchanged_price_is_not_worth_writing(self):
        assert not worth_changing("61.99", Decimal("61.99"), 0.01)

    def test_a_penny_move_counts_by_default(self):
        assert worth_changing("61.99", Decimal("62.00"), 0.01)

    def test_a_larger_threshold_suppresses_small_moves(self):
        assert not worth_changing("61.99", Decimal("62.40"), 1.00)

    def test_a_missing_current_price_always_counts(self):
        assert worth_changing(None, Decimal("10.00"), 0.01)
        assert worth_changing("", Decimal("10.00"), 0.01)
