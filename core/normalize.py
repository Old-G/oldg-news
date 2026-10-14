"""Text cleanup and canonical URLs. Step 1 needs only content_hash; the rest arrives with step 3."""

import hashlib


def content_hash(url: str | None, text: str | None) -> str:
    return hashlib.sha256((url or (text or "")[:500]).encode()).hexdigest()
