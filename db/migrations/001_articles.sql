-- Step 6: mini-articles, daily overview, weekly post. Idempotent: safe to run again.
alter table stories add column if not exists article       jsonb;
alter table stories add column if not exists article_model text;
alter table stories add column if not exists article_at    timestamptz;

alter table digests add column if not exists overview jsonb;

create table if not exists weekly_posts (
  id          bigserial primary key,
  week_start  date not null unique,
  body        jsonb not null,
  model       text,
  tg_msg_id   bigint,
  created_at  timestamptz not null default now()
);
