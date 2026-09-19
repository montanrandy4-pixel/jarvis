"""Alerting. The value is in what it refuses to send twice."""

from __future__ import annotations

import io
import json
from datetime import datetime, timedelta, timezone

import pytest

from storefront import alerts
from storefront.alerts import CRITICAL, INFO, WARNING, Alert, AlertState, Dispatcher


def dispatcher(tmp_path, **over):
    kwargs = dict(
        state=AlertState.load(tmp_path / "alerts.json"),
        cooldown_minutes=60,
        console=False,
        log_path=tmp_path / "alerts.log",
    )
    kwargs.update(over)
    return Dispatcher(**kwargs)


def test_an_alert_fires_once_then_stays_quiet(tmp_path):
    d = dispatcher(tmp_path)
    a = Alert(CRITICAL, "no file on Rate Calculator")
    assert len(d.dispatch([a])[0]) == 1
    assert d.dispatch([a])[0] == []          # same pass, same problem
    assert d.dispatch([a])[0] == []


def test_cooldown_expiry_lets_it_fire_again(tmp_path):
    d = dispatcher(tmp_path, cooldown_minutes=30)
    a = Alert(CRITICAL, "still broken")
    d.dispatch([a])
    stale = datetime.now(timezone.utc) - timedelta(minutes=31)
    d.state.fired[a.key] = stale.isoformat()
    assert len(d.dispatch([a])[0]) == 1


def test_a_problem_that_goes_away_is_reported_resolved(tmp_path):
    d = dispatcher(tmp_path)
    a = Alert(CRITICAL, "no file on Rate Calculator")
    d.dispatch([a])
    sent, resolved = d.dispatch([])
    assert sent == []
    assert resolved == [a.key]
    # and it may fire afresh if it comes back
    assert len(d.dispatch([a])[0]) == 1


def test_severity_routing_keeps_notes_off_the_loud_channels(tmp_path):
    d = dispatcher(tmp_path)
    everything = [Alert(CRITICAL, "c"), Alert(WARNING, "w"), Alert(INFO, "i")]
    assert [a.title for a in d._at_least(everything, CRITICAL)] == ["c"]
    assert [a.title for a in d._at_least(everything, WARNING)] == ["c", "w"]
    assert [a.title for a in d._at_least(everything, INFO)] == ["c", "w", "i"]


def test_critical_alerts_are_sent_before_less_urgent_ones(tmp_path):
    out = io.StringIO()
    d = dispatcher(tmp_path, console=False)
    sent, _ = d.dispatch([Alert(INFO, "quiet"), Alert(CRITICAL, "loud"),
                          Alert(WARNING, "middling")])
    assert [a.severity for a in sent] == [CRITICAL, WARNING, INFO]


def test_distinct_problems_do_not_suppress_each_other(tmp_path):
    d = dispatcher(tmp_path)
    sent, _ = d.dispatch([Alert(CRITICAL, "product A broken"),
                          Alert(CRITICAL, "product B broken")])
    assert len(sent) == 2


def test_state_survives_a_restart(tmp_path):
    path = tmp_path / "alerts.json"
    d1 = Dispatcher(state=AlertState.load(path), console=False)
    a = Alert(CRITICAL, "persistent")
    d1.dispatch([a])
    d2 = Dispatcher(state=AlertState.load(path), console=False)
    assert d2.dispatch([a])[0] == []       # remembered across processes


def test_a_corrupt_state_file_does_not_stop_alerting(tmp_path):
    path = tmp_path / "alerts.json"
    path.write_text("{ broken", "utf-8")
    d = Dispatcher(state=AlertState.load(path), console=False)
    assert len(d.dispatch([Alert(CRITICAL, "x")])[0]) == 1


def test_alerts_are_appended_to_the_log(tmp_path):
    log = tmp_path / "alerts.log"
    d = dispatcher(tmp_path, log_path=log)
    d.dispatch([Alert(CRITICAL, "written down")])
    assert "written down" in log.read_text("utf-8")


def test_a_failing_webhook_does_not_raise(tmp_path):
    # Nothing is listening on this port; dispatch must still return.
    d = dispatcher(tmp_path, webhook_url="http://127.0.0.1:9/nope")
    sent, _ = d.dispatch([Alert(CRITICAL, "webhook target is down")])
    assert len(sent) == 1


def test_webhook_with_no_alerts_is_a_no_op():
    assert alerts.to_webhook([], "http://example.invalid/hook") is True


def test_explicit_key_controls_identity(tmp_path):
    d = dispatcher(tmp_path)
    d.dispatch([Alert(CRITICAL, "order #1001 needs review", key="order:#1001")])
    # Same underlying problem, different wording -- still suppressed.
    sent, _ = d.dispatch([Alert(CRITICAL, "order #1001 flagged", key="order:#1001")])
    assert sent == []
