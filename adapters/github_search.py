"""github_search: GitHub repos gaining stars fast → RawItem (spec: «GitHub — скорость звёзд»).

Once a day, one Search API query per topic: `stars:>{min} created:>{7 days ago} topic:{topic}`, best first.
A repo is emitted once, the first time it shows up; seen repos live in sources.last_external_id (JSON).
Source external_id: `topics:llm,ai-agents,mcp` (optionally `;min_stars:100`). No token: 10 searches a minute.
"""

import json
from datetime import date, datetime, timedelta, timezone

import httpx

from adapters import RawItem

API = "https://api.github.com/search/repositories"
MAX_SEEN = 2000


def parse_spec(external_id: str) -> tuple[list[str], int]:
    """`topics:a,b;min_stars:100` → (topics, min stars)."""
    parts = dict(p.split(":", 1) for p in external_id.split(";") if ":" in p)
    topics = [t.strip() for t in parts.get("topics", "").split(",") if t.strip()]
    return topics, int(parts.get("min_stars", 100))


def to_item(repo: dict, source_key: str, today: date) -> RawItem:
    created = datetime.fromisoformat(repo["created_at"].replace("Z", "+00:00"))
    days = max((today - created.date()).days, 1)
    desc = repo.get("description") or ""
    facts = [f"{repo['stargazers_count']} stars in {days} days since it was created"]
    if repo.get("language"):
        facts.append(repo["language"])
    if repo.get("license") and repo["license"].get("spdx_id") not in (None, "NOASSERTION"):
        facts.append(f"license {repo['license']['spdx_id']}")
    text = "\n".join(p for p in (desc, ", ".join(facts), "Topics: " + ", ".join(repo.get("topics") or [])) if p)
    url = f"https://github.com/{repo['full_name']}"
    title = f"{repo['full_name']}: {desc}" if desc else repo["full_name"]
    return RawItem(source_key, repo["full_name"].lower(), url, title[:200], text, datetime.now(timezone.utc),
                   {"urls": [url], "via": "github_search", "stars": repo["stargazers_count"]})


async def fetch(http: httpx.AsyncClient, external_id: str, cursor: str | None,
                source_key: str) -> tuple[list[RawItem], str, dict]:
    topics, min_stars = parse_spec(external_id)
    today = datetime.now(timezone.utc).date()
    since = (today - timedelta(days=7)).isoformat()
    seen = json.loads(cursor) if cursor else []
    known, items = set(seen), []
    stats = {"queries": 0, "found": 0, "new": 0}
    for topic in topics:
        r = await http.get(API, params={"q": f"stars:>{min_stars} created:>{since} topic:{topic}",
                                        "sort": "stars", "order": "desc", "per_page": 50},
                           headers={"Accept": "application/vnd.github+json"})
        r.raise_for_status()
        stats["queries"] += 1
        for repo in r.json().get("items", []):
            stats["found"] += 1
            key = repo["full_name"].lower()
            if key in known or repo.get("fork") or repo.get("archived"):
                continue
            known.add(key)
            seen.insert(0, key)
            items.append(to_item(repo, source_key, today))
            stats["new"] += 1
    return items, json.dumps(seen[:MAX_SEEN]), stats
