"""Newsletters forwarded to a dedicated mailbox → RawItem. v1 reads Medium Daily Digest: Medium's
personal «For you» picks for Gleb, which no feed exposes.

The mailbox only ever gets what a Gmail filter forwards to it; the main account is never touched.
Messages are read with BODY.PEEK (left unread); the cursor is the last IMAP UID seen.

Anyone can write to the mailbox, so a message counts only when Gmail itself vouches for the sender:
its own Authentication-Results (the topmost, from mx.google.com) show dkim=pass for the From domain,
and From is Medium or one of the trusted forwarders. Links are taken from medium.com only.
"""

import email
import imaplib
import re
from datetime import datetime, timedelta, timezone
from email.message import Message
from email.utils import parseaddr, parsedate_to_datetime
from urllib.parse import urlsplit

import lxml.html

from adapters import RawItem

# medium.com/@author/slug-<hex id> or medium.com/<publication>/slug-<hex id>
MEDIUM_ARTICLE = re.compile(r"^/(@[^/]+|[^/@]+)/[^/]+-[0-9a-f]{8,12}$")
MEDIUM_SENDER = "noreply@medium.com"


def sender(msg: Message, trusted: set[str]) -> str | None:
    """The From address if Gmail verified it (DKIM of the From domain) and it is Medium or a trusted forwarder."""
    addr = parseaddr(msg.get("From") or "")[1].lower()
    if addr != MEDIUM_SENDER and addr not in trusted:
        return None
    # Gmail prepends its own header; anything below it may come from the sender and proves nothing
    ours = next((v for v in msg.get_all("Authentication-Results") or [] if v.strip().startswith("mx.google.com")), "")
    domain = addr.rsplit("@", 1)[-1]
    return addr if re.search(rf"\bdkim=pass\b[^;]*header\.i=@{re.escape(domain)}\b", ours, re.I) else None


def is_medium_digest(msg: Message, html: str, trusted: set[str]) -> bool:
    """Straight from Medium (a filter forward keeps the sender) or forwarded by hand by a trusted address."""
    who = sender(msg, trusted)
    return who == MEDIUM_SENDER or (who is not None and "digest.reader" in html)


def html_body(msg: Message) -> str | None:
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            return part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
    return None


def _text(el) -> str:
    return " ".join("".join(el.itertext()).split()) if el is not None else ""


def parse_medium_digest(html: str, received: datetime | None, source_key: str) -> list[RawItem]:
    """One item per story card: a link that holds the title (h2) and the subtitle (h3); the picture's alt
    carries the title untruncated. Author and publication links have no h2 and are skipped."""
    found: dict[str, dict] = {}
    for a in lxml.html.fromstring(html).iter("a"):
        u = urlsplit(a.get("href") or "")
        host = (u.hostname or "").lower()
        if not (host == "medium.com" or host.endswith(".medium.com")):
            continue
        if not MEDIUM_ARTICLE.match(u.path):
            continue
        card = found.setdefault(f"https://{host}{u.path}", {})
        for img in a.iter("img"):
            if alt := " ".join((img.get("alt") or "").split()):
                card.setdefault("full_title", alt)
        if (h2 := a.find(".//h2")) is not None:
            card["title"], card["subtitle"] = _text(h2), _text(a.find(".//h3"))
    items = []
    for url, card in found.items():
        if not card.get("title"):
            continue
        title = card.get("full_title") or card["title"]
        text = "\n".join(p for p in (title, card["subtitle"]) if p)
        author = urlsplit(url).path.split("/")[1]
        items.append(RawItem(source_key, url, url, title, text, received,
                             {"urls": [url], "via": "medium daily digest", "author": author}))
    return items


def fetch(host: str, user: str, password: str, last_uid: int | None, since_days: int, source_key: str,
          trusted: set[str]) -> tuple[list[RawItem], int | None, dict]:
    """New Medium digests since last_uid (first run: the last since_days days). Blocking: run in a thread."""
    stats = {"messages": 0, "digests": 0, "rejected": 0}
    items: list[RawItem] = []
    newest = last_uid
    m = imaplib.IMAP4_SSL(host)
    try:
        m.login(user, password.replace(" ", ""))
        m.select("INBOX", readonly=True)
        if last_uid:
            _, data = m.uid("search", None, f"UID {last_uid + 1}:*")
        else:
            since = (datetime.now(timezone.utc) - timedelta(days=since_days)).strftime("%d-%b-%Y")
            _, data = m.uid("search", None, f"SINCE {since}")
        uids = [int(x) for x in data[0].split() if int(x) > (last_uid or 0)]  # «n:*» also returns the last one
        for uid in uids:
            _, d = m.uid("fetch", str(uid), "(BODY.PEEK[])")
            msg = email.message_from_bytes(d[0][1])
            stats["messages"] += 1
            newest = max(newest or 0, uid)
            html = html_body(msg)
            if not html or not is_medium_digest(msg, html, trusted):
                stats["rejected"] += bool(html and "digest.reader" in html)  # looks like a digest, sender unverified
                continue
            stats["digests"] += 1
            try:
                received = parsedate_to_datetime(msg.get("Date"))
            except (TypeError, ValueError):
                received = datetime.now(timezone.utc)
            items += parse_medium_digest(html, received, source_key)
    finally:
        try:
            m.logout()
        except Exception:
            pass
    return items, newest, stats
