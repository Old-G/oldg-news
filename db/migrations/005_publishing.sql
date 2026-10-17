-- Pictures for the drafts and publishing to LinkedIn by a button (spec: step 12, «Бот»). Idempotent: safe to run again.
alter table drafts add column if not exists edited_text   text;
alter table drafts add column if not exists published_urn text;
alter table drafts add column if not exists published_at  timestamptz;

create table if not exists story_images (
  story_id    bigint primary key references stories(id),
  kind        text not null check (kind in ('page','generated')),
  source_url  text,
  mime        text not null,
  data        bytea not null,
  created_at  timestamptz not null default now()
);

create table if not exists oauth_tokens (
  provider      text primary key check (provider in ('linkedin')),
  access_token  text not null,
  subject       text not null,
  expires_at    timestamptz not null,
  updated_at    timestamptz not null default now()
);
