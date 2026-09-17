"""A Shopify Admin GraphQL client.

Two things make this more than a wrapper around a POST:

* **Cost-based rate limiting.** Shopify meters GraphQL by query cost against a
  leaky bucket and reports the bucket's state on every response. Rather than
  hitting 429s and backing off, the client reads that state and waits only when
  the bucket is genuinely low.
* **Dry run.** Every mutation can be recorded instead of sent, so a full
  catalogue sync can be inspected before anything touches the live store.
"""

from __future__ import annotations

import json
import logging
import random
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

log = logging.getLogger("shop.client")

# Leave this much of the cost bucket spare before making another call.
COST_HEADROOM = 100
MAX_ATTEMPTS = 5
# Shopify's own guidance: a page of 250 is allowed, but costs scale with it.
PAGE_SIZE = 50


class ShopifyError(RuntimeError):
    """A request failed, or the API reported an error we cannot ignore."""


class UserError(ShopifyError):
    """The API accepted the request but refused the operation."""

    def __init__(self, operation: str, errors: list[dict]):
        self.operation = operation
        self.errors = errors
        detail = "; ".join(
            f"{'.'.join(str(p) for p in e.get('field') or []) or 'error'}: "
            f"{e.get('message', '')}"
            for e in errors
        )
        super().__init__(f"{operation} refused: {detail}")


@dataclass
class PlannedCall:
    """A mutation that a dry run declined to send."""

    operation: str
    variables: dict

    def describe(self) -> str:
        return f"{self.operation} {json.dumps(self.variables, default=str)[:160]}"


@dataclass
class ShopifyClient:
    endpoint: str
    token: str
    dry_run: bool = True
    timeout: float = 30.0
    # Mutations withheld during a dry run, in the order they were attempted.
    planned: list[PlannedCall] = field(default_factory=list)
    calls: int = 0
    _available: float = 1000.0
    _restore_rate: float = 50.0

    # -- transport -------------------------------------------------------

    def _post(self, payload: dict) -> dict:
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode(),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Shopify-Access-Token": self.token,
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8", "replace"))

    def _wait_for_capacity(self, estimated: float) -> None:
        """Pause only when the cost bucket cannot cover the next call."""
        shortfall = (estimated + COST_HEADROOM) - self._available
        if shortfall <= 0 or self._restore_rate <= 0:
            return
        delay = min(shortfall / self._restore_rate, 10.0)
        log.debug("throttling for %.2fs (bucket at %.0f)", delay, self._available)
        time.sleep(delay)

    def _note_cost(self, body: dict) -> None:
        status = (
            body.get("extensions", {}).get("cost", {}).get("throttleStatus") or {}
        )
        if "currentlyAvailable" in status:
            self._available = float(status["currentlyAvailable"])
        if status.get("restoreRate"):
            self._restore_rate = float(status["restoreRate"])

    # -- requests --------------------------------------------------------

    def execute(self, document: str, variables: dict | None = None,
                *, operation: str = "") -> dict:
        """Run a GraphQL document and return its ``data``."""
        operation = operation or _operation_name(document)
        variables = variables or {}

        if self.dry_run and _is_mutation(document):
            self.planned.append(PlannedCall(operation, variables))
            log.info("dry run, not sent: %s", operation)
            return {}

        payload = {"query": document, "variables": variables}
        last_error: Exception | None = None
        for attempt in range(MAX_ATTEMPTS):
            self._wait_for_capacity(50)
            try:
                body = self._post(payload)
            except urllib.error.HTTPError as exc:
                retry_after = _retry_after(exc)
                if exc.code == 429 or exc.code >= 500:
                    last_error = ShopifyError(_http_detail(exc))
                    _sleep_backoff(attempt, retry_after)
                    continue
                raise ShopifyError(_http_detail(exc)) from exc
            except (urllib.error.URLError, OSError) as exc:
                last_error = ShopifyError(f"could not reach {self.endpoint}: {exc}")
                _sleep_backoff(attempt, None)
                continue

            self.calls += 1
            self._note_cost(body)

            errors = body.get("errors")
            if errors:
                if _is_throttled(errors):
                    log.info("throttled by the API; waiting")
                    _sleep_backoff(attempt, None)
                    continue
                raise ShopifyError(
                    "; ".join(e.get("message", str(e)) for e in errors)
                )
            return body.get("data") or {}

        raise last_error or ShopifyError("request failed after several attempts")

    def mutate(self, document: str, variables: dict, *, field_name: str) -> dict:
        """Run a mutation and raise if Shopify reports a user error."""
        data = self.execute(document, variables)
        if not data:
            return {}  # Dry run.
        payload = data.get(field_name) or {}
        errors = payload.get("userErrors") or []
        if errors:
            raise UserError(field_name, errors)
        return payload

    def paginate(self, document: str, variables: dict, *, path: list[str],
                 page_size: int = PAGE_SIZE):
        """Yield every node of a connection, following cursors."""
        cursor = None
        while True:
            data = self.execute(
                document, {**variables, "first": page_size, "after": cursor}
            )
            if not data:
                return
            connection = data
            for key in path:
                connection = connection.get(key) or {}
            for edge in connection.get("edges", []):
                if edge.get("node"):
                    yield edge["node"]
            page = connection.get("pageInfo") or {}
            if not page.get("hasNextPage"):
                return
            cursor = page.get("endCursor")

    # -- health ----------------------------------------------------------

    def check(self) -> dict:
        """Confirm the token works and report who it belongs to."""
        data = self.execute(
            "query { shop { name myshopifyDomain currencyCode ianaTimezone } }"
        )
        return data.get("shop") or {}


def _operation_name(document: str) -> str:
    """Name a document: its operation name, or its first selected field."""
    match = re.search(r"\b(?:query|mutation)\s+([A-Za-z_][A-Za-z0-9_]*)", document)
    if match:
        return match.group(1)
    inner = re.search(r"\{\s*([A-Za-z_][A-Za-z0-9_]*)", document)
    return inner.group(1) if inner else "operation"


def _is_mutation(document: str) -> bool:
    return document.lstrip().startswith("mutation")


def _is_throttled(errors: list[dict]) -> bool:
    return any(
        (e.get("extensions") or {}).get("code") == "THROTTLED"
        or "throttled" in str(e.get("message", "")).lower()
        for e in errors
    )


def _retry_after(exc: urllib.error.HTTPError) -> float | None:
    value = exc.headers.get("Retry-After") if exc.headers else None
    try:
        return float(value) if value else None
    except ValueError:
        return None


def _sleep_backoff(attempt: int, retry_after: float | None) -> None:
    if retry_after:
        time.sleep(min(retry_after, 30.0))
        return
    # Exponential, with jitter so parallel runs do not synchronise.
    time.sleep(min(2**attempt, 16) * (0.5 + random.random() / 2))


def _http_detail(exc: urllib.error.HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8", "replace")
        parsed = json.loads(body)
        detail = parsed.get("errors", parsed)
    except Exception:
        detail = exc.reason
    if exc.code == 401:
        return "the access token was rejected (401). Check it and its scopes."
    if exc.code == 404:
        return (
            "the store or API version was not found (404). Check store_domain "
            "and api_version."
        )
    return f"HTTP {exc.code}: {detail}"
