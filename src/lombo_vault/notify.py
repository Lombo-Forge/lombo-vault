"""Alerts to any webhook. The body carries the text under both "content" (Discord) and "text"
(Slack and compatible), so either works. For ntfy, Gotify or email, point this at an Apprise
or similar bridge. No service is built in and no URL is ever hard-coded.

Webhook and ping URLs are secrets (the path is the key). Errors therefore name only the kind of failure,
never the URL, and any failure, however odd, becomes a NotifyError that callers print as a warning."""

import json
import urllib.error
import urllib.request
from urllib.parse import urlsplit, urlunsplit


class NotifyError(Exception):
    """The webhook did not accept the message."""


def _open(req: urllib.request.Request, timeout: float, what: str) -> None:
    try:
        with urllib.request.urlopen(req, timeout=timeout):  # noqa: S310  # config.py only accepts http(s) URLs
            pass
    except urllib.error.HTTPError as e:
        raise NotifyError(f"{what} failed: HTTP {e.code}") from None
    except Exception as e:  # noqa: BLE001  # bad URL, bad reply, anything: warn, never crash a finished backup
        # No message text: some errors (InvalidURL) quote the secret URL path.
        raise NotifyError(f"{what} failed ({type(e).__name__})") from None


def send(url: str, message: str, timeout: float = 10) -> None:
    body = json.dumps({"content": message, "text": message}).encode()
    try:
        req = urllib.request.Request(  # noqa: S310
            url, data=body, method="POST", headers={"Content-Type": "application/json", "User-Agent": "lombo-vault"}
        )
    except ValueError:
        raise NotifyError("webhook failed (the address is not valid)") from None
    _open(req, timeout, "webhook")


def ping(url: str, failed: bool = False, timeout: float = 10) -> None:
    """Healthchecks-style ping: GET the URL when the job worked, URL + '/fail' when it did not.
    '/fail' goes on the path, so an address with a query string still works."""
    target = url
    if failed:
        parts = urlsplit(url)
        target = urlunsplit(parts._replace(path=parts.path.rstrip("/") + "/fail"))
    try:
        req = urllib.request.Request(target, method="GET")  # noqa: S310  # config.py only accepts http(s) URLs
    except ValueError:
        raise NotifyError("ping failed (the address is not valid)") from None
    _open(req, timeout, "ping")
