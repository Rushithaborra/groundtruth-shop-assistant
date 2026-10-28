"""Security headers for every response, a Content-Security-Policy for the pages, and the same-origin check.

The pages ship their own inline scripts; the policy allows exactly those scripts (by hash) and nothing else, so text
that ever reached the page as HTML still could not run. No CORS headers are sent: other sites can't read the API,
and a POST carrying another site's Origin is refused.
"""

from __future__ import annotations

import base64
import hashlib
import re
from urllib.parse import urlsplit

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cache-Control": "no-store",
}
_INLINE_SCRIPT = re.compile(rb"<script>(.*?)</script>", re.S)
NONE = "'none'"


def content_security_policy(page: bytes) -> str:
    """Allow the page's own inline scripts (by SHA-256) and same-origin requests; nothing loads from elsewhere."""
    hashes = " ".join(
        f"'sha256-{base64.b64encode(hashlib.sha256(body).digest()).decode()}'" for body in _INLINE_SCRIPT.findall(page)
    )
    return (
        "default-src 'none'; "
        f"script-src {hashes or NONE}; "
        "style-src 'self' 'unsafe-inline'; "  # the pages use inline styles; styles can't run code
        "img-src 'self' data:; connect-src 'self'; font-src 'self'; "
        "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    )


def same_origin(origin: str | None, host: str | None) -> bool:
    """True when the request has no Origin (same-site navigation, curl) or its Origin is this server."""
    if not origin:
        return True
    return bool(host) and urlsplit(origin).netloc.lower() == host.strip().lower()
