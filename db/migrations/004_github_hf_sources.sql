-- GitHub stars velocity and Hugging Face trending as source kinds (adapters github_search, hf_trending).
-- Idempotent: safe to run again.
alter table sources drop constraint if exists sources_kind_check;
alter table sources add constraint sources_kind_check
  check (kind in ('telegram','rss','hf_papers','hn','arxiv','manual','imap','page_watch','github_search','hf_trending'));
