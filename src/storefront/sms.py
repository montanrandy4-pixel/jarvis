"""Text messages, via Twilio.

Kept to the REST API over urllib rather than the Twilio SDK, for the same
reason the Shopify client is hand-rolled: one fewer dependency to install, to
pin and to trust on a machine that is going to run unattended for months.

Two things are deliberate:

* **A phone number is personal data.** It is read from the environment or a
  gitignored local config, never from anything that could be committed. The
  example config carries a placeholder.
* **Texts are for things worth a buzz in your pocket.** The default threshold
  is critical-only. A warning can wait for the screen.
"""

from __future__ import annotations

import base64
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .alerts import CRITICAL, RANK, Alert

log = logging.getLogger("storefront.sms")

API = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
# A text is a small window. Say the important thing first.
MAX_BODY = 300


class SmsError(RuntimeError):
    """Twilio refused, or could not be reached."""


@dataclass
class Twilio:
    account_sid: str = ""
    auth_token: str = ""
    from_number: str = ""
    to_number: str = ""

    @property
    def configured(self) -> bool:
        return all([self.account_sid, self.auth_token, self.from_number, self.to_number])

    def send(self, body: str, *, timeout: float = 15.0) -> str:
        """Send one message. Returns the message SID."""
        if not self.configured:
            raise SmsError("twilio is not configured")
        payload = urllib.parse.urlencode({
            "From": self.from_number,
            "To": self.to_number,
            "Body": body[:MAX_BODY],
        }).encode("utf-8")
        credentials = base64.b64encode(
            f"{self.account_sid}:{self.auth_token}".encode("utf-8")
        ).decode("ascii")
        request = urllib.request.Request(
            API.format(sid=urllib.parse.quote(self.account_sid)),
            data=payload,
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read()).get("sid", "")
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = json.loads(exc.read()).get("message", "")
            except Exception:  # noqa: BLE001
                pass
            raise SmsError(f"twilio refused ({exc.code}): {detail or exc.reason}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise SmsError(f"could not reach twilio: {exc}") from exc


def compose(alerts: list[Alert], *, shop: str = "") -> str:
    """One text for a batch. The worst thing first, then a count."""
    if not alerts:
        return ""
    ordered = sorted(alerts, key=lambda a: RANK[a.severity])
    worst = ordered[0]
    prefix = f"{shop}: " if shop else ""
    body = f"{prefix}{worst.title}"
    if worst.detail:
        body += f" -- {worst.detail}"
    if len(ordered) > 1:
        body += f" (+{len(ordered) - 1} more)"
    return body[:MAX_BODY]


def to_sms(alerts: list[Alert], twilio: Twilio, *, shop: str = "",
           minimum: str = CRITICAL) -> bool:
    """Best effort, like every other channel: never raises, never blocks."""
    if not twilio.configured:
        return False
    worth_it = [a for a in alerts if RANK[a.severity] <= RANK[minimum]]
    if not worth_it:
        return True
    try:
        twilio.send(compose(worth_it, shop=shop))
        return True
    except SmsError as exc:
        log.warning("sms failed: %s", exc)
        return False
