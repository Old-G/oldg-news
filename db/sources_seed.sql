-- Feed sources (step 2). Idempotent: an existing source keeps its weight and enabled flag.
--   docker compose exec -T postgres psql -U radar -d radar -f - < db/sources_seed.sql
-- Weights follow the spec tiers: primary 1.3, research and code 1.2, people and communities 1.0.
-- Feeds were checked live on 2026-10-15. No RSS → page_watch: Anthropic, Meta AI, Cohere (Mistral has /news/rss;
-- xAI answers 403 to any client that is not a browser, left out).
-- Left out on purpose: llama.cpp releases (~10 a day), Qwen GitHub blog (moved), Google Developers (undated).

insert into sources (kind, external_id, title, weight) values
  -- labs and vendors
  ('rss', 'https://openai.com/news/rss.xml', 'OpenAI News', 1.3),
  ('rss', 'https://deepmind.google/blog/rss.xml', 'Google DeepMind', 1.3),
  ('rss', 'https://research.google/blog/rss/', 'Google Research', 1.3),
  ('rss', 'https://blog.google/technology/ai/rss/', 'Google AI', 1.3),
  ('rss', 'https://www.microsoft.com/en-us/research/feed/', 'Microsoft Research', 1.3),
  ('rss', 'https://developer.nvidia.com/blog/feed', 'NVIDIA Technical Blog', 1.3),
  ('rss', 'https://blogs.nvidia.com/feed/', 'NVIDIA Blog', 1.3),
  ('rss', 'https://huggingface.co/blog/feed.xml', 'Hugging Face Blog', 1.3),
  ('rss', 'https://machinelearning.apple.com/rss.xml', 'Apple Machine Learning Research', 1.3),
  ('rss', 'https://www.amazon.science/index.rss', 'Amazon Science', 1.3),
  ('rss', 'https://allenai.org/rss.xml', 'Ai2', 1.3),
  -- releases of key repositories
  ('rss', 'https://github.com/anthropics/claude-code/releases.atom', 'Claude Code releases', 1.3),
  ('rss', 'https://github.com/modelcontextprotocol/modelcontextprotocol/releases.atom', 'MCP spec releases', 1.3),
  ('rss', 'https://github.com/modelcontextprotocol/python-sdk/releases.atom', 'MCP Python SDK releases', 1.3),
  ('rss', 'https://github.com/modelcontextprotocol/typescript-sdk/releases.atom', 'MCP TypeScript SDK releases', 1.3),
  ('rss', 'https://github.com/jlowin/fastmcp/releases.atom', 'FastMCP releases', 1.3),
  ('rss', 'https://github.com/openai/openai-agents-python/releases.atom', 'OpenAI Agents SDK releases', 1.3),
  ('rss', 'https://github.com/langchain-ai/langgraph/releases.atom', 'LangGraph releases', 1.3),
  ('rss', 'https://github.com/ollama/ollama/releases.atom', 'Ollama releases', 1.3),
  ('rss', 'https://github.com/vllm-project/vllm/releases.atom', 'vLLM releases', 1.3),
  ('rss', 'https://github.com/unslothai/unsloth/releases.atom', 'Unsloth releases', 1.3),
  -- research
  ('arxiv', 'https://rss.arxiv.org/rss/cs.AI', 'arXiv cs.AI', 1.2),
  ('arxiv', 'https://rss.arxiv.org/rss/cs.CL', 'arXiv cs.CL', 1.2),
  ('arxiv', 'https://rss.arxiv.org/rss/cs.LG', 'arXiv cs.LG', 1.2),
  ('hf_papers', 'https://huggingface.co/api/daily_papers', 'HF daily papers', 1.2),
  -- people and communities
  ('hn', 'https://hnrss.org/frontpage?points=100', 'Hacker News (≥100 points)', 1.0),
  ('rss', 'https://www.reddit.com/r/LocalLLaMA/top.rss?t=day', 'r/LocalLLaMA', 1.0),
  ('rss', 'https://www.reddit.com/r/ClaudeAI/top.rss?t=day', 'r/ClaudeAI', 1.0),
  ('rss', 'https://simonwillison.net/atom/everything/', 'Simon Willison', 1.0),
  ('rss', 'https://www.latent.space/feed', 'Latent Space', 1.0),
  ('rss', 'https://www.interconnects.ai/feed', 'Interconnects', 1.0),
  ('rss', 'https://magazine.sebastianraschka.com/feed', 'Ahead of AI', 1.0),
  ('rss', 'https://lilianweng.github.io/index.xml', 'Lilian Weng', 1.0),
  ('rss', 'https://huyenchip.com/feed.xml', 'Chip Huyen', 1.0),
  ('rss', 'https://medium.com/feed/tag/claude-code', 'Medium #claude-code', 1.0),
  ('rss', 'https://medium.com/feed/tag/ai-agents', 'Medium #ai-agents', 1.0),
  ('rss', 'https://medium.com/feed/tag/mcp', 'Medium #mcp', 1.0),
  ('rss', 'https://medium.com/feed/tag/rag', 'Medium #rag', 1.0),
  ('rss', 'https://medium.com/feed/tag/llm', 'Medium #llm', 1.0),
  ('rss', 'https://medium.com/feed/tag/context-engineering', 'Medium #context-engineering', 1.0),
  ('rss', 'https://levelup.gitconnected.com/feed', 'Level Up Coding', 1.0),
  ('rss', 'https://pub.towardsai.net/feed', 'Towards AI', 1.0),
  ('rss', 'https://ai.gopubby.com/feed', 'AI Advances', 1.0),
  ('rss', 'https://generativeai.pub/feed', 'Generative AI', 1.0),
  ('rss', 'https://ai.plainenglish.io/feed', 'AI in Plain English', 1.0),
  -- Medium publications and authors Gleb saves most to the notes channel (counted 2026-10-15)
  ('rss', 'https://medium.com/feed/data-science-collective', 'Data Science Collective', 1.0),
  ('rss', 'https://medium.com/feed/coding-nexus', 'Coding Nexus', 1.0),
  ('rss', 'https://medium.com/feed/@joe.njenga', 'Joe Njenga', 1.0),
  ('rss', 'https://medium.com/feed/@Micheal-Lanham', 'Micheal Lanham', 1.0),
  ('rss', 'https://medium.com/feed/@visrow', 'Vishal Mysore', 1.0),
  -- labs without a feed: the news page itself, diffed by its article links (adapter page_watch)
  ('page_watch', 'https://www.anthropic.com/news', 'Anthropic News', 1.3),
  ('page_watch', 'https://www.anthropic.com/engineering', 'Anthropic Engineering', 1.3),
  ('page_watch', 'https://ai.meta.com/blog/', 'Meta AI Blog', 1.3),
  ('page_watch', 'https://cohere.com/blog', 'Cohere Blog', 1.3),
  ('rss', 'https://mistral.ai/news/rss', 'Mistral AI News', 1.3)
on conflict (kind, external_id) do nothing;
