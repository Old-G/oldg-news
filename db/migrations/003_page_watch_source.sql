-- News pages without a feed as a source kind (adapter `page_watch`: Anthropic, Meta AI, Cohere).
-- Idempotent: safe to run again.
alter table sources drop constraint if exists sources_kind_check;
alter table sources add constraint sources_kind_check
  check (kind in ('telegram','rss','hf_papers','hn','arxiv','manual','imap','page_watch'));
