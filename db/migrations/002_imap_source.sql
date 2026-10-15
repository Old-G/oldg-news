-- Mail as a source kind (adapter `imap`: Medium Daily Digest forwarded to a dedicated mailbox).
-- Idempotent: safe to run again.
alter table sources drop constraint if exists sources_kind_check;
alter table sources add constraint sources_kind_check
  check (kind in ('telegram','rss','hf_papers','hn','arxiv','manual','imap'));
