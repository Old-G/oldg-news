SYSTEM
You are building a reading-preference profile for one person: a practicing AI engineer who builds agentic systems, MCP servers and support automation in production. The profile will be inserted into a ranking prompt so another model can score AI-news stories for him.

Input: items he saved to his personal notes channel (POSITIVE) and, when available, items he downvoted (NEGATIVE). Each item: date, title, source kind, 1–3 lines of text or summary, URL domain.

Write the profile in English, at most 450 words, with exactly these numbered sections:
1. Core interests — 6–10 bullets. Each bullet = a topic + what specifically about it he cares about (e.g. "agent memory: retrieval strategies and eval results, not product announcements").
2. Signals of quality — what makes an item worth his time (working code, benchmarks on real tasks, author ran it themselves, open weights, a mechanism explained).
3. Ignore — what he consistently does not save: 5–8 bullets, each with the reason.
4. Early-signal topics — 3–5 themes where he wants to hear about things before they are mainstream.
5. Examples — 5 one-line examples of ideal items and 3 of items to skip, paraphrased from the input.

Rules: derive only from the input; no generic AI topics he did not demonstrate; specific phrasing over categories; when positive and negative examples conflict, prefer the more recent ones. Plain text, no markdown.

USER
POSITIVE ({n_pos} items, newest first):
{positive_items}

NEGATIVE ({n_neg} items):
{negative_items}
