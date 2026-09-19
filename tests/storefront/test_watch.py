"""Turning findings into alerts, and the schedules that govern them."""

from __future__ import annotations

from storefront import audit, synthetic, watch
from storefront.alerts import CRITICAL, INFO, WARNING


def test_blockers_become_critical_alerts():
    report = audit.AuditReport(findings=[
        audit.Finding(audit.BLOCKER, "Rate Calculator", "no file", "attach it"),
        audit.Finding(audit.WARNING, "Tax Tracker", "no image", "add one"),
        audit.Finding(audit.NOTE, "Scope Kit", "no tags", "tag it"),
    ])
    severities = [a.severity for a in watch.catalogue_alerts(report)]
    assert severities == [CRITICAL, WARNING, INFO]


def test_alert_keys_are_stable_so_the_same_finding_dedupes():
    f = audit.Finding(audit.BLOCKER, "Rate Calculator", "no file recorded", "fix")
    a = watch.catalogue_alerts(audit.AuditReport(findings=[f]))[0]
    b = watch.catalogue_alerts(audit.AuditReport(findings=[f]))[0]
    assert a.key == b.key


def test_an_unreachable_storefront_is_one_critical_alert_not_many():
    report = synthetic.StorefrontReport(reachable=False, checks=[
        synthetic.Check("home page", False, "timeout"),
        synthetic.Check("product /x", False, "timeout"),
    ])
    out = watch.storefront_alerts(report)
    assert len(out) == 1
    assert out[0].severity == CRITICAL
    assert out[0].key == "storefront:unreachable"


def test_individual_storefront_failures_alert_separately():
    report = synthetic.StorefrontReport(checks=[
        synthetic.Check("home page", True),
        synthetic.Check("product /x price", False, "no price rendered"),
        synthetic.Check("product /x add to cart", False, "no button"),
    ])
    out = watch.storefront_alerts(report)
    assert len(out) == 2
    assert all(a.severity == CRITICAL for a in out)


def test_a_healthy_storefront_raises_nothing():
    report = synthetic.StorefrontReport(checks=[synthetic.Check("home page", True)])
    assert watch.storefront_alerts(report) == []


class _View:
    def __init__(self, name, concerns):
        self.name, self.concerns = name, concerns

    @property
    def needs_review(self):
        return bool(self.concerns)


def test_only_flagged_orders_alert():
    out = watch.order_alerts([_View("#1001", []), _View("#1002", ["payment pending"])])
    assert len(out) == 1
    assert out[0].key == "order:#1002"


class _Summary:
    orders, revenue, currency, average_order = 2, 188.0, "USD", 94.0


def test_sales_alert_is_informational_and_keyed_to_the_totals():
    a = watch.sales_alerts(_Summary())[0]
    assert a.severity == INFO
    assert "188.00" in a.key   # re-fires only when the numbers change


def test_no_orders_means_no_sales_alert():
    class Empty:
        orders, revenue, currency, average_order = 0, 0.0, "USD", 0.0
    assert watch.sales_alerts(Empty()) == []
    assert watch.sales_alerts(None) == []
