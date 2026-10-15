from datetime import datetime, timezone
from email.message import EmailMessage

import pytest

from adapters.imap import is_medium_digest, parse_medium_digest

Q = "?source=email-fb4a25e08c9f-1792021759698-digest.reader-5517fd7b58a6-590f1d528ecb----0-102-----x-1"

# the structure of a real Medium Daily Digest (2026-10-15), with made-up people and stories
DIGEST = f"""
<html><body>
<a href="https://medium.com/@reader1{Q}"><img alt="Reader"></a>
<a href="https://medium.com/?source=email-x-digest.reader-----"><img alt="Medium daily digest"></a>
<a href="https://medium.com/@jane.doe{Q}">Jane Doe</a>
<a href="https://medium.com/gitconnected{Q}">inLevel Up Coding</a>
<a href="https://medium.com/@jane.doe/building-agent-memory-that-lasts-590f1d528ecb{Q}">
  <img alt="Building Agent Memory That Lasts Across Sessions and Restarts"></a>
<a href="https://medium.com/@jane.doe/building-agent-memory-that-lasts-590f1d528ecb{Q}" style="color:inherit">
  <h2>Building Agent Memory That Lasts Across…</h2><div><h3>What three months of production taught us…</h3></div></a>
<a href="https://medium.com/data-science-collective/evals-for-mcp-servers-c9673655b5aa{Q}">
  <h2>Evals for MCP servers</h2><div><h3></h3></div></a>
<a href="https://medium.com/jobs-at-medium/work-at-medium-959d1a85284e">Careers</a>
<a href="https://help.medium.com/hc/en-us">Help</a>
</body></html>
"""
WHEN = datetime(2026, 10, 15, 4, 40, tzinfo=timezone.utc)


def test_parse_medium_digest_takes_story_cards_only():
    items = parse_medium_digest(DIGEST, WHEN, "medium-digest")
    assert [(i.url, i.title, i.text) for i in items] == [
        ("https://medium.com/@jane.doe/building-agent-memory-that-lasts-590f1d528ecb",
         "Building Agent Memory That Lasts Across Sessions and Restarts",
         "Building Agent Memory That Lasts Across Sessions and Restarts\nWhat three months of production taught us…"),
        ("https://medium.com/data-science-collective/evals-for-mcp-servers-c9673655b5aa",
         "Evals for MCP servers", "Evals for MCP servers"),
    ]
    first = items[0]
    assert first.external_id == first.url and first.published_at == WHEN
    assert first.raw == {"urls": [first.url], "via": "medium daily digest", "author": "@jane.doe"}


def mail(sender: str, auth: list[str]) -> EmailMessage:
    m = EmailMessage()
    m["From"] = sender
    for v in auth:
        m["Authentication-Results"] = v
    return m


GMAIL_OK = "mx.google.com; dkim=pass header.i=@gmail.com header.s=x; spf=pass smtp.mailfrom=me@gmail.com"
MEDIUM_OK = "mx.google.com; dkim=pass header.i=@medium.com header.s=x; arc=pass"
TRUSTED = {"me@gmail.com"}


def test_digest_counts_only_when_gmail_verified_the_sender():
    assert is_medium_digest(mail("Medium Daily Digest <noreply@medium.com>", [MEDIUM_OK]), "<html/>", TRUSTED)
    assert is_medium_digest(mail("Me <me@gmail.com>", [GMAIL_OK]), DIGEST, TRUSTED)


@pytest.mark.parametrize("sender, auth, html", [
    ("Medium <noreply@medium.com>", ["mx.google.com; dkim=fail header.i=@medium.com"], DIGEST),  # spoofed From
    ("Medium <noreply@medium.com>", [GMAIL_OK.replace("gmail.com", "evil.com")], DIGEST),       # signed by someone else
    ("Medium <noreply@medium.com>", ["mx.google.com; spf=pass", MEDIUM_OK.replace("mx.google.com", "x")], DIGEST),
    ("Stranger <stranger@gmail.com>", [GMAIL_OK.replace("me@", "stranger@")], DIGEST),         # a real but unknown sender
    ("Me <me@gmail.com>", [GMAIL_OK], "<html>Security alert</html>"),                           # trusted, but not a digest
])
def test_everything_else_is_rejected(sender, auth, html):
    assert not is_medium_digest(mail(sender, auth), html, TRUSTED)


def test_links_off_medium_are_ignored():
    html = DIGEST.replace("https://medium.com/data-science-collective/evals", "https://evil.example/data-science-collective/evals")
    assert [i.title for i in parse_medium_digest(html, WHEN, "x")] == ["Building Agent Memory That Lasts Across Sessions and Restarts"]
