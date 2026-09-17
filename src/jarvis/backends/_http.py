"""Minimal streaming HTTP client built on the standard library.

Deliberately dependency-free: JARVIS talks to a model server over plain HTTP,
and urllib does that perfectly well. It also means the app installs and runs
with nothing but Python.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from urllib.parse import urlparse

log = logging.getLogger("jarvis.http")

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


class HTTPError(RuntimeError):
    """A request failed in a way worth showing the user."""


def _opener(url: str):
    """Build an opener that ignores proxy settings for local model servers."""
    host = (urlparse(url).hostname or "").lower()
    if host in LOCAL_HOSTS:
        # A proxy in the environment must not swallow calls to localhost.
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener()


def request_json(
    url: str,
    payload: dict | None = None,
    *,
    headers: dict | None = None,
    method: str = "GET",
    timeout: float = 30.0,
) -> dict:
    """One request, one JSON response."""
    body = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with _opener(url).open(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raise HTTPError(_explain(exc)) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise HTTPError(f"could not reach {url}: {_reason(exc)}") from exc
    return json.loads(raw) if raw.strip() else {}


def stream_lines(
    url: str,
    payload: dict,
    *,
    headers: dict | None = None,
    timeout: float = 300.0,
    cancel=None,
):
    """POST and yield decoded response lines as they arrive.

    Stops early and closes the connection when ``cancel`` is set, so a barged-in
    reply does not keep the model generating into the void.
    """
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            **(headers or {}),
        },
    )
    try:
        response = _opener(url).open(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        raise HTTPError(_explain(exc)) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise HTTPError(f"could not reach {url}: {_reason(exc)}") from exc

    try:
        for raw_line in response:
            if cancel is not None and cancel.is_set():
                return
            line = raw_line.decode("utf-8", "replace").strip()
            if line:
                yield line
    finally:
        response.close()


def fetch_text(url: str, *, timeout: float = 20.0, max_bytes: int = 400_000,
               headers: dict | None = None) -> str:
    """GET a page and return its body as text, capped in size."""
    request = urllib.request.Request(
        url,
        headers={
            # Some sites serve nothing useful to an unknown client.
            "User-Agent": "Mozilla/5.0 (compatible; JARVIS/1.0)",
            "Accept-Language": "en",
            **(headers or {}),
        },
    )
    try:
        with _opener(url).open(request, timeout=timeout) as response:
            return response.read(max_bytes).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raise HTTPError(_explain(exc)) from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise HTTPError(f"could not fetch {url}: {_reason(exc)}") from exc


def _reason(exc: Exception) -> str:
    reason = getattr(exc, "reason", exc)
    text = str(reason)
    if "refused" in text.lower():
        return "connection refused"
    return text


def _explain(exc: urllib.error.HTTPError) -> str:
    """Pull the server's own error message out of the response body."""
    try:
        body = exc.read().decode("utf-8", "replace")
        data = json.loads(body)
        detail = data.get("error", data.get("message", body))
        if isinstance(detail, dict):
            detail = detail.get("message", str(detail))
    except Exception:
        detail = exc.reason
    return f"HTTP {exc.code}: {detail}"
