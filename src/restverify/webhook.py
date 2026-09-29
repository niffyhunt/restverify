"""Webhook delivery of the verification envelope (I12, R47).

Module boundary (I12): **webhook.py is the only place in the package that opens
an outbound network connection.** It uses :mod:`urllib.request` and nothing
else; there is no retry, no queue, and no background thread — a delivery that
does not succeed within the caller's timeout is reported and abandoned.

Behavior contract:

* Best-effort, always. :func:`deliver` never raises and never changes the
  verification's exit code; it returns ``(ok, one_line)`` for stderr.
* ``https://`` only, with a host. Validation happens at parse time (the CLI
  turns a bad URL or timeout into exit 64 before anything runs), so an
  ``http://`` URL never reaches the network at all.
* Redirects are refused: the opener's redirect handler declines, so a 3xx
  arrives as a non-2xx outcome and is reported as a failure — the envelope is
  for the operator's receiver, not for somebody else's.
* Credentials from URL userinfo are stripped from the request URL before
  sending and never appear in any message.
* The user agent is ``restverify/<version>`` from the package's single version
  source; the body is exactly the ``--json`` envelope bytes.
* Every failure message shows a redacted URL only: scheme, host, port, path —
  never the query string, never the fragment, never userinfo.

Timeouts: one finite ``float``, ``0 < t <= 60`` seconds (parse-time checked).
There is deliberately no retry: the webhook mirrors the run's honesty — one
attempt, one line of truth.
"""
from __future__ import annotations

import argparse
import json
import math
import urllib.error
import urllib.parse
import urllib.request

from . import __version__

MAX_TIMEOUT = 60.0

USER_AGENT = f"restverify/{__version__}"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Decline every redirect: the 3xx response is returned as-is, which the
    caller reports as a delivery failure (I12: redirects are refused)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


def parse_url(text: str) -> str:
    """Validate a webhook URL at parse time; return it stripped.

    Raises :class:`ValueError` with a teaching message for: empty input, any
    scheme other than ``https``, or a missing host. The CLI maps this to exit
    64 before any command runs, so a bad URL can never reach the network.
    """
    if text is None:
        raise ValueError("a webhook URL is required (https://host/path)")
    url = text.strip()
    if not url:
        raise ValueError("a webhook URL is required (https://host/path)")
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https":
        raise ValueError(
            f"the webhook URL must start with https:// (got {parts.scheme or 'no scheme'}://); "
            "the run envelope carries verification results, so it never travels in cleartext")
    if not parts.hostname:
        raise ValueError("the webhook URL needs a host, e.g. https://collector.example/hook")
    return url


def parse_timeout(text) -> float:
    """Validate a webhook timeout at parse time; return it as float seconds.

    Accepts a finite number with ``0 < t <= 60`` (the default is 10). Raises
    :class:`ValueError` for anything else; the CLI maps this to exit 64.
    """
    try:
        value = float(text)
    except (TypeError, ValueError):
        raise ValueError(
            f"webhook timeout must be a number of seconds (got {text!r}); "
            f"allowed: 0 < t <= {MAX_TIMEOUT:.0f}") from None
    if not math.isfinite(value) or value <= 0 or value > MAX_TIMEOUT:
        raise ValueError(
            f"webhook timeout must be a finite number with 0 < t <= {MAX_TIMEOUT:.0f} "
            f"(got {text})")
    return value


def url_arg(text: str) -> str:
    """argparse type= wrapper: the teaching message survives verbatim."""
    try:
        return parse_url(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def timeout_arg(text) -> float:
    """argparse type= wrapper: the teaching message survives verbatim."""
    try:
        return parse_timeout(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def redact(url: str) -> str:
    """The display form of a webhook URL: scheme://host[:port]/path only.

    Userinfo, query string and fragment are dropped — a signed query parameter
    or an embedded password must never reach a log line (I12 rule).
    """
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or ""
    if ":" in host and not host.startswith("["):   # bare IPv6
        host = f"[{host}]"
    netloc = host
    if parts.port:
        netloc = f"{host}:{parts.port}"
    path = parts.path or ""
    return urllib.parse.urlunsplit((parts.scheme or "https", netloc, path, "", ""))


def envelope_bytes(payload: dict) -> bytes:
    """The exact bytes of the ``--json`` envelope: the same serialization the
    CLI prints (indent 2, non-ASCII kept, one trailing newline), so what the
    receiver stores is byte-for-byte what the operator saw."""
    return (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def deliver(url: str, body: bytes, timeout: float,
            ssl_context=None) -> tuple[bool, str]:
    """POST ``body`` to ``url`` once. Returns ``(ok, one_line)``; never raises.

    ``ssl_context`` is an injection seam for tests (trust a test certificate);
    production callers pass ``None`` and get urllib's default TLS handling.
    Success is a 2xx status; everything else — connect refusal, TLS failure,
    timeout, redirect, 4xx/5xx — is ``(False, one_line)`` for stderr.
    """
    safe = redact(url)
    parts = urllib.parse.urlsplit(url)
    # Rebuild without userinfo: credentials in the URL are never transmitted.
    request_url = urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc.rpartition("@")[-1], parts.path, "", ""))
    request = urllib.request.Request(
        request_url, data=body, method="POST",
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT})
    handlers: list = [_NoRedirect()]
    if ssl_context is not None:
        handlers.append(urllib.request.HTTPSHandler(context=ssl_context))
    opener = urllib.request.build_opener(*handlers)
    try:
        response = opener.open(request, timeout=timeout)
        try:
            code = getattr(response, "status", None) or response.getcode()
            response.read(4096)          # release the connection politely
        finally:
            response.close()
        if 200 <= code < 300:
            return True, f"delivered to {safe} (HTTP {code})"
        if 300 <= code < 400:
            return False, f"failed: {safe} answered HTTP {code} — redirects are refused"
        return False, f"failed: {safe} answered HTTP {code}"
    except urllib.error.HTTPError as exc:
        try:
            exc.close()
        except Exception:
            pass
        if 300 <= exc.code < 400:
            return False, f"failed: {safe} answered HTTP {exc.code} — redirects are refused"
        return False, f"failed: {safe} answered HTTP {exc.code}"
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, TimeoutError):
            return False, f"failed: {safe} timed out after {timeout:g}s"
        return False, f"failed: could not reach {safe} ({reason})"
    except TimeoutError:
        return False, f"failed: {safe} timed out after {timeout:g}s"
    except OSError as exc:
        return False, f"failed: could not reach {safe} ({exc.strerror or exc})"
