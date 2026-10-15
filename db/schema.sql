create extension if not exists vector;

-- Источники
create table sources (
  id               bigserial primary key,
  kind             text not null check (kind in ('telegram','rss','hf_papers','hn','arxiv','manual','imap','page_watch','github_search','hf_trending')),
  external_id      text not null,              -- tg channel id / URL фида / 'manual'
  title            text,
  weight           real not null default 1.0,  -- доверие к источнику, 0.5–1.5
  is_label_source  boolean not null default false, -- канал-заметки
  enabled          boolean not null default true,
  last_external_id text,                       -- min_id для Telethon / последний guid
  last_fetched_at  timestamptz,
  created_at       timestamptz not null default now(),
  unique (kind, external_id)
);

-- Истории: кластер items об одном и том же
create table stories (
  id              bigserial primary key,
  canonical_url   text,
  title           text,
  summary         text,            -- 1–2 предложения, EN
  why_it_matters  text,            -- 1 предложение, EN
  category        text check (category in ('release','paper','tool','infra','agent','business','other')),
  tags            text[] not null default '{}',
  heat            int not null default 1,      -- число различных source_id
  first_seen_at   timestamptz not null,
  last_seen_at    timestamptz not null,
  centroid        vector(1536),
  prefilter_sim   real,            -- max cosine к позитивным центроидам вкуса
  llm_score       real,            -- 0–10
  llm_reason      text,
  final_score     real,
  is_early_signal boolean not null default false,
  status          text not null default 'new' check (status in ('new','digested','starred','dismissed')),
  enriched_at     timestamptz,
  scored_at       timestamptz,
  article         jsonb,           -- мини-статья RU: {title, lead, details[], why_it_matters, try, caveats}
  article_model   text,
  article_at      timestamptz,
  created_at      timestamptz not null default now()
);
create index stories_centroid_hnsw on stories using hnsw (centroid vector_cosine_ops);
create index stories_last_seen on stories (last_seen_at desc);
create index stories_status on stories (status);

-- Сырые единицы: один пост / одна запись фида
create table items (
  id            bigserial primary key,
  source_id     bigint not null references sources(id),
  story_id      bigint references stories(id),
  external_id   text not null,
  url           text,                         -- канонический главный URL
  urls          text[] not null default '{}',
  title         text,
  text          text,
  lang          text,
  published_at  timestamptz,
  fetched_at    timestamptz not null default now(),
  content_hash  text not null,
  is_ad         boolean not null default false,
  embedding     vector(1536),
  raw           jsonb,
  unique (source_id, external_id)
);
create index items_story on items (story_id);
create index items_url on items (url);
create index items_published on items (published_at desc);

-- Дайджесты и их состав
create table digests (
  id            bigserial primary key,
  for_date      date not null unique,
  overview      jsonb,                          -- обзор дня RU: {summary, direction}
  header_msg_id bigint,                         -- пост-обзор в канале
  sent_at       timestamptz,
  created_at    timestamptz not null default now()
);

create table digest_entries (
  digest_id   bigint not null references digests(id),
  story_id    bigint not null references stories(id),
  position    int not null,
  section     text not null check (section in ('top','early')),
  tg_msg_id   bigint,                         -- сообщение с кнопками
  primary key (digest_id, story_id)
);

-- Еженедельный пост «Неделя: куда движемся»
create table weekly_posts (
  id          bigserial primary key,
  week_start  date not null unique,           -- понедельник недели
  body        jsonb not null,                 -- {intro, trends[], watch}
  model       text,
  tg_msg_id   bigint,
  created_at  timestamptz not null default now()
);

-- Обратная связь
create table feedback (
  id          bigserial primary key,
  story_id    bigint not null references stories(id),
  digest_id   bigint references digests(id),
  action      text not null check (action in ('up','down','star','missed')),
  origin      text not null check (origin in ('bot','audience','notes','manual')),
  created_at  timestamptz not null default now()
);
create index feedback_story on feedback (story_id);

-- Черновики постов
create table drafts (
  id          bigserial primary key,
  story_id    bigint not null references stories(id),
  lang        text not null check (lang in ('ru','en')),
  channel     text not null check (channel in ('tg','x','linkedin')),
  text        text not null,
  model       text,
  created_at  timestamptz not null default now()
);

-- Профиль вкуса: версии текста и центроиды
create table taste_profiles (
  id            bigserial primary key,
  version       int not null unique,
  profile_text  text not null,                -- вставляется в промпт score
  built_from    jsonb,                        -- {"stars": 214, "downs": 37, "until": "2026-10-10"}
  is_active     boolean not null default false,
  created_at    timestamptz not null default now()
);

create table taste_centroids (
  id          bigserial primary key,
  profile_id  bigint not null references taste_profiles(id),
  polarity    text not null check (polarity in ('pos','neg')),
  centroid    vector(1536) not null,
  n_items     int
);

-- Лог запусков
create table runs (
  id          bigserial primary key,
  job         text not null,                  -- fetch | daily | wave | weekly | weekly_taste
  started_at  timestamptz not null default now(),
  finished_at timestamptz,
  stats       jsonb,                          -- items_new, stories_new, enriched, scored, tokens, usd
  error       text
);
