from datetime import datetime, timezone
from email.message import EmailMessage

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


def test_digest_is_recognized_directly_and_when_forwarded_by_hand():
    direct = EmailMessage()
    direct["From"] = "Medium Daily Digest <noreply@medium.com>"
    by_hand = EmailMessage()
    by_hand["From"] = "Gleb <someone@gmail.com>"
    assert is_medium_digest(direct, "<html></html>")
    assert is_medium_digest(by_hand, DIGEST)
    assert not is_medium_digest(by_hand, "<html>Security alert</html>")
