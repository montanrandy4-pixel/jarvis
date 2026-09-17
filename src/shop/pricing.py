"""Turning a supplier's cost into a shelf price.

Deliberately boring and deterministic: the same cost and the same rules always
produce the same price, so a sync that changes nothing writes nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal


@dataclass
class Price:
    amount: Decimal
    compare_at: Decimal | None = None
    # Set when the price needs a human before it can be published.
    hold: str = ""

    @property
    def held(self) -> bool:
        return bool(self.hold)

    def as_strings(self) -> tuple[str, str | None]:
        return (
            f"{self.amount:.2f}",
            f"{self.compare_at:.2f}" if self.compare_at else None,
        )


def _money(value) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def apply_charm(amount: Decimal, ending: str) -> Decimal:
    """Round up to a psychological price ending such as .99 or .95."""
    if not ending or ending in {"none", "off"}:
        return _money(amount)
    try:
        cents = Decimal(ending)
    except Exception:
        return _money(amount)
    if cents >= 1:  # e.g. "5" means round up to the next multiple of 5.
        step = cents
        return _money((amount / step).to_integral_value(rounding="ROUND_CEILING") * step)
    whole = amount.to_integral_value(rounding="ROUND_FLOOR")
    candidate = whole + cents
    if candidate < amount:
        candidate += 1
    return _money(candidate)


def price_for(cost: float, rules, *, suggested: float | None = None) -> Price:
    """Work out what to charge for something that costs ``cost``."""
    base = _money(cost) * _money(rules.markup) + _money(rules.handling)
    amount = apply_charm(base, rules.charm_ending)

    floor = _money(rules.floor) if rules.floor else None
    if floor and amount < floor:
        amount = apply_charm(floor, rules.charm_ending)

    hold = ""
    if rules.ceiling and amount > _money(rules.ceiling):
        hold = (
            f"price {amount} is above the ceiling of {_money(rules.ceiling)}"
        )
    if amount <= _money(cost):
        hold = f"price {amount} would not cover the cost of {_money(cost)}"

    compare_at = None
    if rules.compare_at_multiplier and rules.compare_at_multiplier > 1:
        compare_at = apply_charm(
            amount * _money(rules.compare_at_multiplier), rules.charm_ending
        )
    elif suggested is not None and _money(suggested) > amount:
        # The supplier's own RRP makes an honest "was" price.
        compare_at = _money(suggested)

    return Price(amount=amount, compare_at=compare_at, hold=hold)


def worth_changing(current: str | float | None, proposed: Decimal,
                   min_change: float) -> bool:
    """Is the difference big enough to be worth an API write?"""
    if current is None or current == "":
        return True
    try:
        existing = _money(current)
    except Exception:
        return True
    return abs(existing - proposed) >= _money(max(min_change, 0.01))
