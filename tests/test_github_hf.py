import asyncio
import json
from datetime import date

import httpx

from adapters import github_search, hf_trending


def run(handler, coro_fn):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await coro_fn(http)
    return asyncio.run(go())


def repo(name, stars, created="2026-10-12T08:00:00Z", **kw):
    return {"full_name": name, "stargazers_count": stars, "created_at": created, "description": f"{name} desc",
            "language": "Python", "license": {"spdx_id": "MIT"}, "topics": ["llm", "mcp"], "fork": False,
            "archived": False, **kw}


def test_github_spec_and_item():
    assert github_search.parse_spec("topics:llm, mcp;min_stars:250") == (["llm", "mcp"], 250)
    assert github_search.parse_spec("topics:llm") == (["llm"], 100)
    it = github_search.to_item(repo("Org/Tool", 954), "k", date(2026, 10, 15))
    assert (it.external_id, it.url, it.title) == ("org/tool", "https://github.com/Org/Tool", "Org/Tool: Org/Tool desc")
    assert it.text == "Org/Tool desc\n954 stars in 3 days since it was created, Python, license MIT\nTopics: llm, mcp"


def test_github_emits_each_repo_once_across_topics_and_runs():
    pages = {"llm": [repo("a/one", 900), repo("b/two", 300)], "mcp": [repo("a/one", 900), repo("c/fork", 500, fork=True)]}
    queries = []

    def handler(req):
        q = req.url.params["q"]
        queries.append(q)
        return httpx.Response(200, json={"items": pages[q.rsplit(":", 1)[1]]})

    items, cursor, stats = run(handler, lambda h: github_search.fetch(h, "topics:llm,mcp", None, "k"))
    assert [i.external_id for i in items] == ["a/one", "b/two"] and stats["queries"] == 2
    assert queries[0].startswith("stars:>100 created:>") and queries[0].endswith("topic:llm")
    pages["llm"].append(repo("d/new", 150))
    items, _, _ = run(handler, lambda h: github_search.fetch(h, "topics:llm,mcp", cursor, "k"))
    assert [i.external_id for i in items] == ["d/new"]


def test_hf_readme_cleanup_and_description():
    readme = "---\nlicense: mit\ntags: [x]\n---\n# Model\n<!-- hidden -->\n![img](a.png)\n\n\n\nUses 2B params."
    assert hf_trending.strip_front_matter(readme) == "# Model\n\nUses 2B params."
    assert hf_trending.describe("model", {"id": "google/gemma", "likes": 10, "downloads": 5, "pipeline_tag": "text-generation",
                                          "numParameters": 2_500_000_000}) == \
        ("google/gemma", "trending model on Hugging Face, 10 likes, 5 downloads, text-generation, 2.5B parameters")
    assert hf_trending.describe("space", {"id": "u/app", "likes": 3, "title": "App", "shortDescription": "Does X"}) == \
        ("App (u/app)", "Does X\ntrending space on Hugging Face, 3 likes")


def test_hf_emits_new_entries_once_with_readme():
    listing = {"recentlyTrending": [{"repoType": "dataset", "repoData": {"id": "org/data", "likes": 9}},
                                    {"repoType": "dataset", "repoData": {"id": "org/secret", "likes": 1, "private": True}}]}

    def handler(req):
        if req.url.path == "/api/trending":
            return httpx.Response(200, json=listing)
        if req.url.path == "/datasets/org/data/raw/main/README.md":
            return httpx.Response(200, text="---\na: b\n---\nTraces of agent runs.")
        return httpx.Response(404)

    url = "https://huggingface.co/api/trending?type=dataset&limit=20"
    items, cursor, stats = run(handler, lambda h: hf_trending.fetch(h, url, None, "k"))
    assert [(i.external_id, i.url) for i in items] == [("dataset:org/data", "https://huggingface.co/datasets/org/data")]
    assert items[0].text.endswith("Traces of agent runs.") and json.loads(cursor) == ["dataset:org/data"]
    items, _, _ = run(handler, lambda h: hf_trending.fetch(h, url, cursor, "k"))
    assert items == []
