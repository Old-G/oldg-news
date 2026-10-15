# Radar v1 — спецификация

Oct 10, 2026 · @Gleb

## Цель и границы v1

Radar v1 — личный фильтр: из сотни источников в один ежедневный дайджест на 5–7 историй — обзор дня и по мини-статье на историю, с кнопками обратной связи и черновиками постов по звёздочке. Публикация в канал своих постов в v1 — руками, система даёт отбор и текст.

Один пайплайн — несколько выходов: история хранится структурно и на английском (что это, почему важно, ссылки, теги, heat), а тексты для канала (RU-мини-статья, обзор дня, обзор недели) и черновики для RU-Telegram и EN-каналов генерируются отдельными промптами, не переводом друг друга. Мини-статья самодостаточна: читатель не открывает оригиналы, все факты, цифры и механизм — в тексте.

Что делает v1:

- Читает все Telegram-каналы, на которые ты подписан, плюс HF papers, HN и arXiv (cs.AI, cs.CL, cs.LG).
- Склеивает дубли в истории и считает heat — число независимых источников.
- Ранжирует по профилю вкуса, собранному из твоего канала-заметок, и по твоим 👍/👎/⭐.
- Публикует дайджест в твой существующий Telegram-канал волнами в течение дня: утром обзор «Главное за сутки» + «Куда движемся», затем по мини-статье на русском на каждую из 5–7 историй, вечером «Ранние сигналы»; по воскресеньям — пост «Неделя: куда движемся». Бот в личке — пульт управления и статистика.
- По ⭐ присылает черновики твоих постов: RU для Telegram и EN для X/LinkedIn (EN отключаются `DRAFTS_EN = false`).

Не входит в v1: MCP-сервер, автопубликация, X как источник, GitHub trending, веб-интерфейс, мультипользовательский режим, платный тир.

Принципы: минимум компонентов (Python + планировщик + один бот), каждый порог живёт в конфиге, каждое решение системы пишется в БД — через 2–3 недели смотрим на данные, а не на ощущения.

## Архитектура и стек

Три сервиса в одном docker-compose: worker (планировщик и весь пайплайн), bot (aiogram, long polling) и postgres (Postgres 18 с pgvector). LLM и эмбеддинги — OpenAI API. n8n в v1 не нужен: пайплайн с эмбеддингами и кластеризацией проще держать в Python.

&#91;embedded content: компоненты Radar v1 · 4 вида источников, worker из 6 стадий, Supabase, LLM, бот\]

Worker — единственный процесс, который пишет в stories и зовёт LLM по расписанию. Бот — один аккаунт на два процесса с общим токеном: worker шлёт дайджест и черновики, bot-процесс принимает нажатия и команды. Черновики по ⭐ тоже идут в LLM — стрелка на схеме одна, чтобы не загромождать.

Стек:

- Python 3.12, uv, pydantic-settings для конфига.
- Telethon — пользовательская сессия для чтения подписок. Bot API не подходит: бот не видит чужие каналы.
- feedparser + httpx — RSS и HTTP.
- aiogram 3 — бот, inline-кнопки, long polling. Без вебхука и FastAPI в v1.
- APScheduler внутри worker — расписание. Системный cron — если проще.
- Postgres 18 с pgvector (образ pgvector/pgvector:pg18; на 19 — после GA и появления образа) как сервис docker-compose: том для данных, ежедневный pg\_dump в отдельный том; доступ через psycopg напрямую.
- trafilatura — основной текст статьи по ссылке для мини-статей (через тот же httpx-клиент с защитой от SSRF).
- OpenAI: дешёвая модель для enrich/score (пакетно), сильная — для мини-статей, обзоров и черновиков постов, модель эмбеддингов на 1536 измерений. Имена моделей задаёшь ты; живут в конфиге, не в коде. Если модель эмбеддингов даёт другую размерность — поменять vector(1536) в схеме.
- Docker Compose: сервисы postgres, worker и bot, общий .env.

Структура репозитория:

```text
radar/
  config.py            # настройки, пороги, модели
  adapters/            # telegram.py, rss.py (hf, hn, arxiv — конфиги фидов)
  core/
    normalize.py       # чистка текста, канонические URL
    embed.py           # эмбеддинги батчами
    cluster.py         # поиск соседей, склейка в stories
    taste.py           # профиль вкуса: центроиды + текст
    enrich.py          # summary / why / tags (LLM, JSON)
    score.py           # prefilter + LLM-оценка + формула
    digest.py          # отбор 5–7, волны, рендер сообщений
    article.py         # материалы истории (полный текст ссылки) → мини-статья, обзор дня, обзор недели
    drafts.py          # RU/EN черновики по ⭐
  bot/                 # aiogram: дайджест, callback'и, /add, /missed
  db/                  # schema.sql, migrations/, queries.py
  prompts/             # taste_profile.md, taste_update.md, enrich.md, score.md, article_ru.md, overview_ru.md, weekly_ru.md, draft_ru.md, draft_en.md
  jobs/                # fetch.py, daily.py, weekly.py, weekly_taste.py, bootstrap_taste.py
  docker-compose.yml
```

Переменные окружения: TG\_API\_ID, TG\_API\_HASH, TG\_SESSION (строковая сессия Telethon), NOTES\_CHANNEL\_ID, BOT\_TOKEN, OWNER\_CHAT\_ID, DIGEST\_CHANNEL\_ID, DATABASE\_URL, OPENAI\_API\_KEY, EMBED\_MODEL, CHEAP\_MODEL, STRONG\_MODEL.

Безопасность:

- Telethon-сессия даёт полный доступ к твоему аккаунту: только в секретах, не в репозитории; 2FA включена; FloodWait обрабатывается сном на указанное время.
- Бот приватный: OWNER\_CHAT\_ID проверяется на каждом апдейте, остальные игнорируются.

## Источники и адаптеры

Четыре вида источников, один интерфейс: каждый адаптер отдаёт список `RawItem(source_key, external_id, url, title, text, published_at, raw)`. Всё остальное делает общий пайплайн.

| Источник | Как читаем | Объём в день | Особенности |
| --- | --- | --- | --- |
| Telegram-подписки | Telethon, `iter_dialogs()` → только каналы → `iter_messages(min_id=last_seen)` | сотни | реклама отсекается эвристикой: `#реклама`, `erid`, `промокод`, ссылка-только-на-канал; медиа без текста пропускаем |
| Канал-заметки | тот же Telethon, `is_label_source = true` | единицы | каждый пост = положительная метка вкуса (feedback `star`, origin `notes`); forwarded-from сохраняем — это ещё и источник |
| HF papers | `https://huggingface.co/api/daily_papers` (JSON) | 20–40 | есть upvotes — идёт в raw, в v1 не влияет на score |
| Hacker News | `hnrss.org/frontpage` или Firebase API `topstories` с фильтром score ≥ 100 | 30–60 | url = внешняя ссылка, не обсуждение; обсуждение кладём в `urls` |
| arXiv | RSS `rss.arxiv.org/rss/cs.AI`, `cs.CL`, `cs.LG` | 300–600 | только abstract; в LLM попадает лишь после prefilter по эмбеддингам |
| Почта (IMAP) | отдельный ящик (`IMAP_USER`), куда фильтр Gmail пересылает Medium Daily Digest; читается в каждом `fetch`, курсор — последний UID | 10–15 статей | персональные рекомендации Medium («For you»), которых нет ни в одном фиде; из письма берутся ссылка, заголовок и подзаголовок, текст статьи — на шаге 9; основной почтовый ящик Radar не видит |
| Ручной ввод | команда `/add <url>` в боте, форвард в канал-заметки | единицы | `/add` создаёт item с `source = manual` и сразу тянет историю в ближайшую волну сегодняшнего дайджеста |

Правила для Telegram-адаптера:

- Первый запуск: `backfill_hours = 48`, дальше только `min_id` по каждому каналу (`sources.last_external_id`).
- Между каналами пауза 0.5–1 с; `FloodWaitError` → спать `e.seconds + 5`.
- Альбомы (grouped\_id) склеиваем в один item.
- Текст: `message.message` + текст ссылок из `entities` (скрытые URL под словами — главный источник ссылок в RU-каналах).
- Пересланные посты: `fwd_from.channel_id` пишем в raw, это даёт дедуп между каналами.

Канонические URL (normalize): убрать `utm_*`, `ref`, `si`, `fbclid`; `arxiv.org/pdf/2510.01234v2` → `arxiv.org/abs/2510.01234`; `github.com/org/repo/anything` → `github.com/org/repo`; `huggingface.co/papers/<id>` → `arxiv.org/abs/<id>`; `x.com` и `twitter.com` → один домен; короткие ссылки (`t.co`, `bit.ly`) разворачиваем HEAD-запросом с таймаутом 5 с.

Добавить источник = одна строка в `sources` плюс, для RSS/JSON, конфиг фида. Код адаптера не трогается.

## Стартовый список источников

Цель — не максимум источников, а чтобы всё важное появлялось минимум в двух независимых. Старт — 50–80 источников, дальше только по `/missed`. Вес: первоисточники выше, агрегаторы ниже — они поздние. URL фидов проверяются при добавлении автообнаружением (`<link rel="alternate">`), где фида нет — RSSHub или `page_watch`.

Первоисточники — вес 1.3:

| Источник | Адаптер | Как |
| --- | --- | --- |
| Блоги OpenAI, Anthropic, Google DeepMind, Meta AI, Mistral, Cohere, Microsoft Research | rss / page\_watch | RSS там, где есть (Mistral — `/news/rss`); иначе `page_watch`: состояние страницы новостей — набор ссылок на статьи (на том же хосте, ровно на уровень ниже её пути, без tag/category/page), diff с виденными → новые статьи; каждая новая скачивается один раз за заголовком, датой (`article:published_time`, JSON-LD, `<time>`) и текстом; старше `FEED_MAX_AGE_H` — только запоминается; первый прогон лишь запоминает текущие ссылки. Сейчас: Anthropic News и Engineering, Meta AI Blog, Cohere Blog. xAI отдаёт 403 всему, кроме браузера, — не берём |
| Changelog API: Anthropic, OpenAI, Google AI | page\_watch | diff страницы release notes: источник с адресом `…#sections`, состояние — набор разделов с датой в заголовке (h2/h3 с id); каждый новый раздел — отдельный item с якорем в URL, чтобы разделы одной страницы не склеивались. Сейчас: Claude Platform release notes, Gemini API changelog. OpenAI changelog отдаёт 403 — его релизы приходят через OpenAI News RSS |
| Hugging Face blog | rss | `huggingface.co/blog/feed.xml` |
| Релизы ключевых репозиториев: MCP SDK, LangGraph, vLLM, llama.cpp, Ollama, Claude Code, OpenAI Agents SDK | rss | `github.com/<org>/<repo>/releases.atom` |

Исследования и код — вес 1.2:

| Источник | Адаптер | Как |
| --- | --- | --- |
| arXiv cs.AI, cs.CL, cs.LG | rss | `rss.arxiv.org/rss/<cat>`, только после prefilter |
| HF daily papers | hf\_papers | `huggingface.co/api/daily_papers` |
| HF trending models / datasets / spaces | hf\_trending | `huggingface.co/api/trending?type=model&limit=20`, то же для `dataset` и `space` (лимит API — 20; `api/models?sort=trending` с 2026 отвечает 400); item — когда репозиторий впервые попал в список, текст моделей и датасетов — из README |
| GitHub — скорость звёзд | github\_search | Search API: `stars:>100 created:>{7 дней} topic:<тема>`, по запросу на тему (`llm`, `ai-agents`, `agent`, `mcp`, `claude-code`; `topic:agents` пуст), раз в день (`GITHUB_EVERY_H`); item — когда репозиторий впервые попал в выдачу |
| Semantic Scholar — цитирования свежих работ по тегам | rss / API | по ключевым словам, еженедельно; v1.1 |

Люди и сообщества — вес 1.0:

| Источник | Адаптер | Как |
| --- | --- | --- |
| Telegram-подписки | telegram | Telethon, все каналы |
| Hacker News | hn | `hnrss.org/frontpage` или Firebase `topstories`, score ≥ 100 |
| Reddit r/LocalLLaMA, r/MachineLearning, r/ClaudeAI, r/artificial | rss | `reddit.com/r/<sub>/top.rss?t=day`, User-Agent обязателен |
| Substack и блоги: Import AI, Latent Space, Interconnects, Ahead of AI, One Useful Thing, Simon Willison, Chip Huyen, Lilian Weng | rss | `<name>.substack.com/feed` или собственный домен |
| Medium | rss | теги `medium.com/feed/tag/ai-agents`, `tag/llm`, `tag/mcp`; авторы `medium.com/feed/@user`; фид отдаёт \~10 последних постов и обрезанный текст |
| Bluesky — списки исследователей и инженеров | bluesky | открытый AT Protocol API, бесплатно; замена X |
| YouTube: Latent Space, Lex Fridman, каналы конференций | rss + transcript | `youtube.com/feeds/videos.xml?channel_id=…` → `youtube-transcript-api` → текст в item |
| Product Hunt — AI-категория | rss | `producthunt.com/feed` |

Агрегаторы — вес 0.7, страховка от пропусков:

| Источник | Адаптер | Как |
| --- | --- | --- |
| TLDR AI, The Rundown AI, Ben's Bites, Hacker Newsletter | rss / imap | RSS через RSSHub или отдельный почтовый ящик + IMAP; Kill the Newsletter как альтернатива |

X — отдельное решение. Официальный API платный и урезанный, цены меняются; сторонние скраперы дешёвые, но серая зона и живут недолго. X не источник уникальных новостей: всё важное появляется в TG-каналах, на Bluesky и HN за часы, а TG-подписки репостят твиты. В v1 три вещи: ссылки на твиты внутри любых постов разворачиваются через `api.fxtwitter.com/<user>/status/<id>` (текст, тред, медиа — бесплатно); Bluesky как открытая замена; ручной форвард в канал-заметки. Платный доступ к X — только если `/missed` покажет утечку оттуда.

Новые типы адаптеров к v1: `github_search`, `hf_trending`, `bluesky`, `page_watch` (хэш страницы + diff → item), `imap` (письма из выделенного ящика → item). Всё остальное закрывает `rss`. RSSHub — один self-hosted контейнер с сотнями готовых маршрутов (GitHub trending, Product Hunt, Medium, Bluesky, публичные TG-каналы, X-списки с куки) — ставится рядом с worker в docker-compose.

## Схема БД

Одиннадцать таблиц: `sources` и `items` — сырьё, `stories` — склеенные истории (единица ранжирования и публикации; мини-статья хранится на истории), `digests` + `digest_entries` — что и как показано (обзор дня — на `digests`), `weekly_posts` — посты «Неделя: куда движемся», `feedback` — твои реакции, `drafts` — черновики, `taste_profiles` + `taste_centroids` — версии вкуса, `runs` — лог запусков. Изменения схемы на живой базе — файлами `db/migrations/NNN_*.sql` (идемпотентно: `add column if not exists`, `create table if not exists`); `schema.sql` всегда равен этому разделу. Эмбеддинг хранится на item и усредняется в centroid истории; HNSW-индекс нужен только на `stories`.

```sql
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
```

Два запроса, на которых держится пайплайн.

Поиск истории для нового item (склейка):

```sql
select id, 1 - (centroid <=> $1) as sim
from stories
where last_seen_at > now() - interval '7 days'
order by centroid <=> $1
limit 3;
```

Prefilter кандидатов на сегодня — близость к позитивным центроидам активного профиля:

```sql
select s.id, max(1 - (s.centroid <=> c.centroid)) as sim
from stories s
cross join taste_centroids c
where c.profile_id = $1 and c.polarity = 'pos'
  and s.status = 'new'
  and s.last_seen_at > now() - interval '36 hours'
group by s.id
order by sim desc
limit 60;
```

## Пайплайн по шагам

Job'ы по расписанию: `fetch` каждые 2 часа (шаги 1–4), `daily` утром до первой волны — подготовка дайджеста (шаги 5–9), `wave` по времени волн — публикация (шаг 10), `weekly` по воскресеньям (шаг 13). Шаги 11–12 — по событиям от бота. Каждый шаг идемпотентен: повторный запуск ничего не дублирует.

1. **Fetch.** Каждый включённый источник → адаптер → `RawItem`. Вставка в `items` с `on conflict do nothing`. Обновить `last_external_id` и `last_fetched_at`.
2. **Normalize.** Канонический URL, чистка текста (ссылки из entities в текст, убрать эмодзи-мусор и подписи «подписывайся»), `lang` через fast-detect, `content_hash = sha256(url or text[:500])`. Реклама → `is_ad = true`, дальше не идёт.
3. **Embed.** Батчами по 100: `embedding(title + "\n" + text[:1500])`. Items без текста и url пропускаются.
4. **Cluster.** Для каждого нового item:
   - точное совпадение `url` с `items.url` другой истории за 7 дней → та же история;
   - иначе ближайшая история по `centroid` (запрос из раздела «Схема БД»): `sim ≥ CLUSTER_SIM` (старт 0.80, см. «Пороги») → присоединить;
   - совпадение URL склеивает, только если ссылка указывает на одну вещь (не ссылка на Telegram-канал и не корень сайта) и `sim ≥ URL_MATCH_MIN_SIM`;
   - если в истории уже есть item того же источника, новый присоединяется только при `sim ≥ SAME_SOURCE_SIM` (почти дословный повтор): ежедневные шаблоны одного канала — разные истории;
   - items выключенных источников не эмбеддятся и не кластеризуются;
   - иначе новая история: `title`, `canonical_url`, `first_seen_at`, `centroid = embedding`.
   - При присоединении: `centroid = mean(embeddings)`, `heat = count(distinct source_id)`, `last_seen_at = now()`. Items из канала-заметок дополнительно пишут `feedback(star, notes)`.
5. **Prefilter.** Кандидаты = `status = 'new'`, видны за 36 часов. `prefilter_sim` = max cosine к позитивным центроидам активного профиля. В LLM идут: top-`PREFILTER_N` (старт 60) по `prefilter_sim`, плюс всё с `heat ≥ 3`, плюс всё из manual и канала-заметок. arXiv-одиночки ниже порога `PREFILTER_MIN_SIM` (старт 0.30) отсекаются здесь — это защищает от 500 абстрактов в день.
6. **Enrich.** Для каждого кандидата без `enriched_at`: промпт `enrich` → JSON `{summary, why_it_matters, category, tags, title_clean, is_ad, confidence}`. Вход — заголовок, текст лучшего item (самый длинный, до `ENRICH_TEXT_CHARS` = 2000 символов), список URL, число источников и их виды. Пакет по 10 историй в один запрос. `is_ad = true` → `status = 'dismissed'`, дальше не идёт.
7. **Score.** Промпт `score`: активный `profile_text` + пакет из 15 кандидатов → `{story_id, score 0–10, reason}`. Затем формула:
   - `heat_norm = min(heat, 8) / 8`
   - `src_w` = средний `weight` источников истории, нормированный к 0–1: `(avg − 0.5) / 1.0`, с отсечкой в [0, 1]
   - `final = 0.6 * llm_score/10 + 0.25 * heat_norm + 0.15 * src_w`
   - штраф `-0.15`, если у истории уже есть `down`; `+0.1`, если `up`.
   - `is_early_signal = heat ≤ 2 and llm_score ≥ 7 and источник ∈ {hn, hf_papers, arxiv, github (релизы и github_search), hf_trending}`, и среди источников нет Telegram — новое, ещё не подхваченное каналами.
8. **Select.** Отбор: top по `final` с ограничением ≤ `MAX_PER_CATEGORY` (2) на категорию, всего `DIGEST_N` (старт 6), section `top`; ручные `/add` идут первыми и в лимит не входят. Плюс до `EARLY_N` (3) историй с `is_early_signal` из не попавших в top — section `early`. Создать `digests` + `digest_entries`, выбранным историям `status = 'digested'`. Отбор делается один раз в день; не выбранные остаются `new` ещё сутки — могут набрать heat и попасть завтра; старше 48 часов → `dismissed`.
9. **Articles + overview.** Для каждой выбранной истории без `article`: собрать материалы → промпт `article_ru` сильной моделью → JSON `{title, lead, details[], why_it_matters, try, caveats}` в `stories.article` (+ `article_model`, `article_at`). Материалы истории, первым — основной источник:
   - основная ссылка (`canonical_url`, если указывает на одну вещь): полный текст страницы через httpx-клиент с защитой от SSRF + trafilatura; GitHub-репозиторий → README (raw); x.com → `api.fxtwitter.com`; arXiv → abstract; t.me и страницы, которые не отдались, — пропускаются, статья пишется по items;
   - тексты всех items истории (без рекламы и без повторов одного и того же текста);
   - всё вместе обрезается до `ARTICLE_SOURCE_CHARS` (20 000 символов).

   Затем обзор дня: промпт `overview_ru` по мини-статьям секции `top` → `{summary, direction}` в `digests.overview`. Готовое не пересчитывается: повторный запуск генерирует только недостающее.
10. **Waves.** Публикация в канал тремя волнами (формат и состав — в разделе «Бот»). Волна сначала догоняет подготовку (шаги 5–9, идемпотентно — если утренний `daily` упал или не успел), затем шлёт `digest_entries` с пустым `tg_msg_id`, чья волна наступила; `tg_msg_id` пишется сразу после отправки — повторный запуск волны ничего не дублирует. История, для которой мини-статья так и не получилась, публикуется короткой карточкой из полей enrich (`title_clean`, `summary`, `why_it_matters`).
11. **Feedback.** Callback от кнопки → `feedback(action, origin='bot')`. `down` → `status = 'dismissed'`. `star` → `status = 'starred'` и шаг 12.
12. **Drafts.** По `star` (только твоему, origin bot): worker раз в `DRAFTS_POLL_S` (60 с) находит ⭐ без черновиков за последние `DRAFTS_WINDOW_H` (72 ч) → промпт `draft_ru` сильной моделью (с `DRAFTS_EN` — ещё `draft_en`); на вход — английская запись истории (не RU-мини-статья, чтобы EN не читался как перевод) и выдержка из материалов истории до `DRAFT_SOURCE_CHARS` (6000 символов) → бот присылает RU-черновик (с `DRAFTS_EN` — ещё EN для X и EN для LinkedIn) отдельными сообщениями, готовыми к копированию → по записи на текст в `drafts`. Сначала отправка, потом запись: сбой между ними повторит черновики, но не потеряет. Опрос без новых ⭐ ничего не пишет, даже строку в `runs`.
13. **Weekly.** В воскресенье: истории всех дайджестов недели (пн–вс) с их мини-статьями (`title`, `lead`, `why_it_matters`) и твоими ⭐/👍 (origin bot) → промпт `weekly_ru` сильной моделью → `{intro, trends[], watch}` в `weekly_posts` → пост «Неделя: куда движемся» в канал. Неделя публикуется один раз (`week_start` unique, `tg_msg_id`).

Еженедельно, `weekly_taste`: пересборка профиля вкуса (раздел «Профиль вкуса и обратная связь»).

Расписание (время Минска): `fetch` в :00 каждые 2 часа; `daily` (подготовка) в 07:00 (`DIGEST_PREPARE_AT`); волны дайджеста в 07:30, 13:00 и 19:00 (`DIGEST_WAVES`); `weekly` в воскресенье 20:00, после вечерней волны (`WEEKLY_POST_AT`); `weekly_taste` в воскресенье 09:00. Каждый запуск пишет строку в `runs` со статистикой и стоимостью.

## Промпты

Девять промптов, инструкции все на английском (так стабильнее у любой модели), даже когда ответ на русском; лежат в `prompts/*.md` с `{placeholders}`. Общие правила: ответ только JSON там, где указано; парсер срезает \`\`\`-ограждения; при невалидном JSON — один повтор с добавкой «Return valid JSON only»; токены и стоимость каждого вызова пишутся в `runs.stats`. Мнение в черновиках всегда помечено как варианты — твоя позиция остаётся твоей. Мини-статьи и обзоры — только факты из источников: чего в источниках нет, то названо «не указано», а не додумано.

**1. `taste_profile.md` — профиль вкуса** (bootstrap из канала-заметок; дешёвой моделью достаточно, сильная даёт профиль точнее)

```text
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
```

**1a. `taste_update.md` — еженедельное обновление профиля вкуса** (сильная модель, `weekly_taste`)

```text
SYSTEM
You maintain a reading-preference profile for one person: a practicing AI engineer who builds agentic systems, MCP servers and support automation in production. The profile is inserted into a ranking prompt so another model can score AI-news stories for him. Each week you get the current profile and the signals he gave since it was written; you return the updated profile.

Signals: POSITIVE — items he saved to his notes channel, starred or upvoted in his digest, or reported as missed by the digest (he found them elsewhere and wanted them); NEGATIVE — items he downvoted in his digest. Each item: date, title, source kind, 1–3 lines of text or summary, URL domain.

Update rules:
- Start from the current profile and keep its five numbered sections, its wording and everything the signals do not contradict. The current profile was built from years of his notes and corrected by hand; a topic missing from one week of signals is not evidence that he lost interest.
- Change only with evidence: a new interest or a sharper description of an existing one needs at least two positive signals; a new Ignore bullet needs at least two negative signals, or one negative that directly contradicts a current interest.
- Remove a bullet only when negative signals contradict it. Never remove an Ignore bullet because of positives about a different topic.
- Prefer refining an existing bullet over adding a new one; at most 10 core interests and 8 Ignore bullets.
- A single item never becomes a core interest or an example by itself.
- Keep it under 450 words, English, plain text, no markdown. Return the profile only — no preface, no list of changes.

USER
CURRENT PROFILE (v{version}):
{profile_text}

NEW SIGNALS since {since}, newest first.
POSITIVE ({n_pos} items):
{positive_items}

NEGATIVE ({n_neg} items):
{negative_items}
```

**2. `enrich.md` — структурирование истории** (дешёвая модель, пакет по 10)

```text
SYSTEM
You turn raw AI-news items into structured story records. Output strict JSON only: a JSON array in the same order as the input, no prose, no code fences.

For each story return:
{
  "story_id": <int, copied from input>,
  "title_clean": <string, ≤ 90 chars, neutral, no clickbait, no emoji>,
  "summary": <1–2 sentences, ≤ 50 words, what it is and what is new; English regardless of input language>,
  "why_it_matters": <1 sentence, ≤ 25 words, for a practicing AI engineer; concrete, never "could be useful">,
  "category": <one of: release | paper | tool | infra | agent | business | other>,
  "tags": <2–5 lowercase tags, prefer this set: llm, agents, mcp, rag, memory, evals, inference, open-weights, fine-tuning, multimodal, voice, coding-agents, safety, infra, product, funding, research>,
  "is_ad": <true if the text promotes a course, channel, service or event and carries no substantive news>,
  "confidence": <0–1, whether the source text is enough to describe the story>
}

Rules: never invent numbers, names or capabilities absent from the text. If the text is only a link plus a line, keep the summary minimal and set confidence ≤ 0.4. Keep model and project names exactly as written. When several source texts describe the same story, merge them; do not list differences between sources.

USER
[{"story_id": 123, "title": "...", "text": "...", "urls": ["..."], "n_sources": 4, "source_kinds": ["telegram", "hn"]}, ...]
```

**3. `score.md` — оценка релевантности** (дешёвая модель, пакет по 15)

```text
SYSTEM
You score AI-news stories for ONE specific reader. Output strict JSON only: an array of {"story_id": int, "score": 0–10, "reason": "≤ 20 words"} in input order.

READER PROFILE
{profile_text}

Scoring guide:
- 9–10: he would stop what he is doing to read it — squarely on a core interest, substantive, likely to change what he builds or thinks.
- 7–8: clearly relevant, worth two minutes today.
- 4–6: tangential, or the kind of news he already knows; fine to skip.
- 0–3: off-profile, promotional, repetitive, or a thin link-only item.

Weigh: match to core interests (dominant); novelty against the Ignore section; substance (code, benchmarks, open weights, first-hand results); timeliness. Do NOT reward popularity or number of sources — that is handled outside this prompt. Do NOT reward big-company names by default. A paper with a real result on his interests outranks a product launch off-profile.

USER
[{"story_id": 123, "title_clean": "...", "summary": "...", "why_it_matters": "...", "category": "paper", "tags": ["agents", "memory"], "n_sources": 2, "first_seen_hours_ago": 9}, ...]
```

**4. `article_ru.md` — мини-статья для канала** (сильная модель, по каждой истории дайджеста)

```text
SYSTEM
You write one short mini-article in Russian for a Telegram channel about AI engineering. The reader is a practicing AI engineer who builds agentic systems, MCP servers and support automation in production. He will NOT open the original sources: the article must stand on its own — the facts, numbers, names and mechanism he needs are in your text. He reads it on a phone: short, concrete, scannable.

Output strict JSON only, no prose, no code fences:
{
  "title": <Russian headline, ≤ 70 chars: the point, not the press-release name; product names as written; no emoji, no clickbait>,
  "lead": <1–2 short sentences, ≤ 250 chars: what happened, who did it, the one thing that is new>,
  "details": <array of 3–4 strings, each ≤ 140 chars and one fact: how it works, a key number, version, availability — only what the sources state>,
  "why_it_matters": <one sentence, ≤ 180 chars: what concretely changes for someone building agents>,
  "try": <one sentence, ≤ 140 chars: what he could test or check first; null if nothing actionable>,
  "caveats": <one sentence, ≤ 140 chars: the main limit, open question or contradiction; null if none>
}

Rules:
- Facts only from SOURCES. Never invent numbers, names, benchmarks or capabilities. Do not list what the sources leave out (price, license, availability) unless its absence matters for using the thing — then say so once, in caveats.
- All fields together: 500–1000 characters. Plain text inside fields, no markdown. Every sentence carries a fact; no filler, no restating the title.
- Russian; technical terms stay in English the way engineers say them (agent, eval, fine-tuning, MCP, context window). Numbers and names exactly as in the sources.
- Calm practitioner voice: no hype words, no exclamation marks.
- Several sources describe one story: merge them into one account, do not list or compare the sources.

USER
Story: {n_sources} sources ({source_kinds}); category: {category}; tags: {tags}
SOURCES:
{sources}
```

**5. `overview_ru.md` — обзор дня «Главное за сутки» + «Куда движемся»** (сильная модель, раз в день)

```text
SYSTEM
You write the opening post of a daily AI-engineering digest in Russian. The reader is an AI engineer who builds agentic systems; below are today's stories, already written up, each with its own post. This post is the 20-second version, read on a phone. Output strict JSON only: {"summary": "...", "direction": "..."}.

summary: 1–2 short sentences, ≤ 280 chars: the day's main theme, naming 2–3 concrete things (products, numbers). Do not retell every story — the table of contents lists them.
direction: one sentence, ≤ 160 chars, «куда движемся»: the trend today's stories point to and what it means for people building agents. Grounded in today's stories; if they share nothing, name the strongest single signal instead.

Rules: Russian, technical terms in English; facts only from the stories; calm, no hype, no exclamation marks; plain text, no markdown.

USER
Date: {date}
Stories:
{stories}
```

**6. `weekly_ru.md` — пост «Неделя: куда движемся»** (сильная модель, по воскресеньям)

```text
SYSTEM
You write a weekly post in Russian, «Неделя: куда движемся», for a Telegram channel about AI engineering. Input: the week's digest stories with their write-ups, marked when the channel author starred or upvoted them. Output strict JSON only:
{
  "intro": <1–2 sentences: the shape of the week>,
  "trends": [
    {"name": <trend, ≤ 60 chars>,
     "evidence": <2–3 sentences naming the stories that show it>,
     "direction": <1–2 sentences: where it is heading and what it means for agent builders>,
     "try": <one sentence: what to test or watch next>}
  ],
  "watch": <one sentence: one weak signal worth watching next week; null if none>
}

Rules: 3–4 trends, each backed by at least two of the week's stories (one strong story only when nothing else connects); prefer trends behind starred and upvoted stories; facts only from the input; Russian with English technical terms; calm, no hype, no exclamation marks; plain text inside fields.

USER
Week: {week}
Stories (★ starred, 👍 upvoted by the author):
{stories}
```

**7. `draft_ru.md` — пост для Telegram** (сильная модель, по ⭐)

```text
SYSTEM
You write a Telegram post in Russian for the channel of an AI engineer who builds agentic systems in production. The post is in his voice — a practitioner, not a news agency.

Voice: first person, direct, no hype, no exclamation marks, at most one emoji and only at the start; short sentences; technical terms stay in English (MCP, agent, eval, fine-tuning); numbers and names exact; light irony allowed.

Structure, ≤ 900 characters total:
1. First line — the point, not the headline: what changed and why someone building agents should care. Never "Вышла новая модель".
2. 2–4 lines of substance: what it is and one concrete detail — a number, a mechanism, a limitation.
3. His take — NOT written as his opinion. Give two one-line candidate angles, marked "Вариант A:" and "Вариант B:" (what he would test first, what he doubts, what it means for agent builders). He will keep one or replace both.
4. Links: 1–2 bare URLs on separate lines, primary source first.

Rules: nothing beyond the story record; a point that needs a fact absent from the record becomes a question; no hashtags; no calls to subscribe.

USER
{story_record_json}
Best source excerpt:
{best_item_text}
```

**8. `draft_en.md` — посты для X и LinkedIn** (сильная модель, по ⭐; не вызывается при `DRAFTS_EN = false`)

```text
SYSTEM
From one story record, draft two English posts for an AI engineer who ships agentic systems and MCP servers in production. Output JSON only: {"x": "...", "linkedin": "..."}.

Voice: specific, calm, practitioner. No hype words (game-changer, huge, insane), no emoji. No hashtags on X; at most 2 on LinkedIn. American spelling.

X post: ≤ 260 characters. First clause is the insight; one concrete number or mechanism; link last on its own line.
LinkedIn post: 80–140 words. First line stands alone as the hook (what changed for people building agents), then 2–3 short paragraphs: what it is, what is actually new, what to test first; end with one question to readers; link on the last line.

Both posts include a placeholder "[TAKE: A — ... / B — ...]" with two one-line candidate angles for his own opinion. Never state an opinion as his outside that placeholder.

Rules: facts only from the record; names and numbers exact. An EN post is not a translation of the RU post — it is written from the record for a global practitioner audience.

USER
{story_record_json}
Best source excerpt:
{best_item_text}
```

## Бот

Worker публикует дайджест в твой существующий Telegram-канал (DIGEST\_CHANNEL\_ID, бот — админ канала) тремя волнами в течение дня, чтобы читать порциями, а не всё разом: утром (07:30) — пост-обзор дня и мини-статьи 1–3, днём (13:00) — мини-статьи 4–`DIGEST_N`, вечером (19:00) — секция «Ранние сигналы». Одна мини-статья — один пост со своим рядом кнопок: кнопки привязаны к сообщению, реакция ставится в одно касание с телефона. Мини-статья самодостаточна — открывать оригиналы не нужно, ссылки внизу только для проверки, поэтому превью ссылки выключено. Дайджест с первого дня — публичный контент, поэтому в постах нет ничего личного; статистика и команды живут в личке бота. Все тексты — HTML-разметка Telegram, ≤ 4096 символов на сообщение; всё, что пришло от модели, экранируется.

Оформление — под телефон: главное видно сразу, подробности свёрнуты в раскрывающуюся цитату (`<blockquote expandable>`), у категории иконка (🚀 release · 📄 paper · 🛠 tool · ⚙️ infra · 🤖 agent · 💼 business · 📰 other), теги — хештеги канала (`coding-agents` → `#coding_agents`), по ним работает поиск в канале.

Пост-обзор дня, первым в утренней волне (строка «Вчера: 👍 3 · 👎 1 · ⭐ 1» уходит отдельно в личку бота, не в канал):

```text
☕️ <b>Главное за 16 октября</b>

{overview.summary}

🧭 <b>Куда движемся:</b> {overview.direction}

<b>В выпуске</b>
1️⃣ {article.title}
2️⃣ {article.title}
…

<i>⏰ 4–6 — в 13:00 · 🌱 ранние сигналы — в 19:00</i>
```

Оглавление собирается кодом из заголовков мини-статей секции `top`; последняя строка — только про то, что ещё будет (нет второй волны или ранних сигналов — нет и упоминания).

Мини-статья (поля из `stories.article`; пустые `try` и `caveats` не выводятся; строка «🔥 N источников» — при heat ≥ 2):

```text
🛠 <b>{title}</b>
<i>#1 · 🔥 4 источника · #agents #mcp</i>

{lead}

💡 {why_it_matters}

<blockquote expandable>▸ {details[0]}
▸ {details[1]}
…

🧪 <b>Попробовать:</b> {try}
⚠️ <b>Оговорки:</b> {caveats}</blockquote>
🔗 <a href="{url}">{домен}</a> · <a href="{url}">{домен}</a>
```

В строке 🔗 — до 3 ссылок: основная, затем другие конкретные ссылки истории, затем пост в Telegram-канале, откуда история пришла. Если мини-статьи нет (шаг 10), пост — короткая карточка: та же шапка с `title_clean`, `{summary}`, `💡 {why_it_matters}`, ссылки.

Кнопки под каждой мини-статьёй: `👍` · `👎` · `⭐ пост`. После нажатия кнопки заменяются на отметку (`✓ 👍`), чтобы не нажимать дважды; `⭐` после `👍` разрешена, обратный ход — нет. В канале кнопки видят и подписчики: нажатия с твоего user id идут в профиль вкуса (origin bot), чужие пишутся как origin audience и в ранжирование не входят — это отдельный сигнал об аудитории; ⭐ срабатывает только для тебя.

Секция «Ранние сигналы» — те же мини-статьи, заголовок секции отдельным сообщением: `🌱 Ранние сигналы` / `Уже на HN, arXiv и HF — в каналах ещё нет`. Здесь ценны `👎` не меньше `👍`: они быстрее всего настраивают порог сигнала.

Пост «Неделя: куда движемся», воскресенье, без кнопок:

```text
🗓 <b>Неделя: куда движемся</b>
<i>13–19 окт</i>

{intro}

1️⃣ <b>{trend.name}</b>
{trend.evidence}
🧭 {trend.direction}
🧪 {trend.try}

2️⃣ …

👀 <b>Присмотреться:</b> {watch}
```

По `⭐` бот присылает RU-черновик (с `DRAFTS_EN` — ещё EN X-черновик и EN LinkedIn-черновик) — чистым текстом без разметки, чтобы копировалось как есть. Публикация руками в v1.

Команды:

- `/add <url>` — ручной item, история попадает в ближайшую волну вне очереди (мини-статья пишется перед отправкой).
- `/missed <url>` — «я увидел это не у Radar»: пишет `feedback(missed)`; если URL уже есть в базе — помечает, что история была, но не прошла отбор. Главная метрика качества, см. раздел «Метрики».
- `/today` — прислать в личку уже опубликованное сегодня: обзор и мини-статьи.
- `/stats` — за 7 дней: историй всего, в дайджесте, 👍/👎/⭐, missed, стоимость LLM.
- `/source <tg-ссылка> on|off` — включить или выключить источник без захода в БД.

Команды в личке проверяют `chat.id == OWNER_CHAT_ID`; всё остальное молча игнорируется; callback'и под постами в канале принимаются от любого, с разделением по origin, как выше.

## Профиль вкуса и обратная связь

Профиль — две вещи сразу: текст для промпта `score` и набор центроидов для дешёвого prefilter по эмбеддингам. Обе версионируются в `taste_profiles` / `taste_centroids`, активна ровно одна версия.

Bootstrap (`jobs/bootstrap_taste.py`, один раз):

1. Telethon выгружает канал-заметки — твой существующий канал со статьями с Medium и других источников — целиком: все посты, пересланные посты, ссылки. Это готовая разметка «мне интересно» — сотни примеров до первого дня работы системы.
2. Каждый пост → normalize → embed → `items` с `source.is_label_source = true` и `feedback(star, origin='notes')`.
3. Центроиды: k-means по эмбеддингам, `k = 5` (старт; при 300+ примерах — 8). Пишутся как `polarity = 'pos'`. Негативных пока нет.
4. Текст профиля: промпт `taste_profile` на 300 самых свежих постов. Если не влезает в контекст — чанки по 100, профиль по каждому, затем ещё один вызов «merge these into one profile with the same sections».
5. Прочитать профиль глазами и поправить руками, что явно не так. Это единственная ручная правка; дальше профиль живёт на данных.

Еженедельное обновление (`jobs/weekly_taste.py`):

- Позитив = `star` из бота + `up` + посты канала-заметок + `missed` (особый позитив — ниже); негатив = `down`.
- Пересчёт центроидов: `pos` по всем позитивным, `neg` по негативным (`k = TASTE_NEG_K` = 3, и только когда 👎 набралось не меньше трёх — один случайный 👎 не вкус), и в prefilter идёт `sim_pos − 0.5 * sim_neg`.
- Текст профиля не переписывается заново, а обновляется: промпт `taste_update` получает активный профиль и сигналы, пришедшие после него (пост канала-заметок датируется самим постом, нажатие в боте — нажатием, не больше `TASTE_RECENT_N` = 300), и меняет только то, что они подтверждают. Без новых сигналов текст остаётся как есть, без вызова LLM. Так ручная правка bootstrap и давние интересы не теряются из-за одной тихой недели (решение 15.10.2026: перегенерация по окну 90 дней на превью выбросила spec-driven разработку, AI для рынков и почти весь раздел Ignore). Новая версия сразу активна, старые сохраняются (откат — `jobs.bootstrap_taste --activate N`). Диф двух версий присылается в бот одним сообщением — чтобы видеть, куда уехал вкус; там же — причины missed за неделю. `--preview` собирает версию, не активируя её.
- `missed` — особый сигнал: история была в базе, но не прошла отбор. Для каждой такой истории пишем в `runs.stats` (строка `job = 'missed'`), где именно её отсекло: `prefilter`, `score`, `digest_limit` (оценка проходила, но не влез лимит дайджеста или категории), а также `not_in_db` (ни в одном источнике — повод добавить источник; для учёта заводится история-заглушка со статусом `dismissed`), `in_digest`, `down`, `not_yet` (отбора ещё не было), `no_digest` (дайджест в те дни не собирался). Бот отвечает причиной сразу. Если из 10 missed 7 отсёк prefilter — порог `PREFILTER_MIN_SIM` слишком высокий, это видно без гадания.

Почему два слоя, а не один LLM-скоринг на всё: prefilter за копейки режет 500 абстрактов arXiv до 60 кандидатов, а LLM видит только тех, кто прошёл. При этом всё с `heat ≥ 3` минует prefilter — чтобы мейнстрим-новость не потерялась только потому, что не похожа на старые заметки.

## Порядок сборки

Восемь шагов, каждый заканчивается проверяемым результатом. Первый дайджест в боте появляется после шага 5 — ещё без enrich и score, но уже читаемый. Публикация в канал начинается с шага 6, в формате мини-статей (решение 15.10.2026: короткие карточки шага 5 в канал не идут); остальное дособираешь по ходу.

1. **Схема + Telegram-адаптер.** Готово, когда: `schema.sql` накатан, Telethon-сессия сохранена в `.env`, `jobs/fetch.py` за один прогон кладёт посты за 48 часов из всех каналов в `items`, повторный прогон ничего не дублирует.
2. **RSS/JSON-адаптеры.** Готово, когда: HF papers, HN и три фида arXiv попадают в `items` с правильными `published_at` и каноническими URL; `/add <url>` создаёт item.
3. **Normalize + embed + cluster.** Готово, когда: у каждого item есть `embedding`, у каждой истории — `centroid` и `heat`; выборочная проверка 30 историй — не больше 2 неверных склеек и не больше 3 несклеенных дублей. Иначе двигать `CLUSTER_SIM`.
4. **Bootstrap вкуса.** Готово, когда: канал-заметки выгружен, центроиды посчитаны, текст профиля прочитан и поправлен, `taste_profiles.is_active = true`.
5. **Бот: дайджест и кнопки.** Готово, когда: в канале волнами появляются заголовок + истории (пока отсортированные по `prefilter_sim` и `heat`), кнопки пишут в `feedback`, `/today` и `/stats` работают. Публикация руками начинается здесь.
6. **Enrich + score + мини-статьи.** Готово, когда: `llm_reason` объясняет оценку; дайджест строится по `final_score` с лимитом на категорию, секция «Ранние сигналы» появляется; мини-статьи читаются как написанные человеком и самодостаточны — понятно, что произошло, как работает и почему важно, без открытия оригинала, и в них нет фактов, которых нет в источниках; обзор дня называет конкретику, а не «много новостей»; превью нового формата пришло в личку и ты сказал «ок» — только после этого волны публикуют в канал; воскресный пост «Неделя: куда движемся» сначала тоже превью в личку.
7. **Черновики по ⭐.** Готово, когда: по звёздочке приходят три текста, RU-пост публикуется с правкой не дольше пары минут, EN-посты не читаются как перевод RU.
8. **Weekly taste + `/missed`.** Готово, когда: воскресный прогон создаёт новую версию профиля и присылает диф, `/missed` пишет причину отсечения в `runs.stats`.

До шага 6 стоимость LLM — только эмбеддинги; это ничего не блокирует. Шаги 7–8 можно перемежать с ежедневной публикацией: система работает на каждом промежуточном состоянии.

## Метрики, пороги и стоимость

Одна главная метрика — доля полезного в дайджесте: `(👍 + ⭐) / показано`. Вторая — `missed` за неделю: сколько важного прошло мимо. Всё остальное — для диагностики.

| Метрика | Как считается | Цель через 3 недели |
| --- | --- | --- |
| useful\_rate | (👍 + ⭐) / историй в дайджесте, 7 дней | ≥ 50% |
| star\_rate | ⭐ в день | ≥ 1 — это топливо для постов |
| missed | `/missed` за 7 дней | ≤ 2, у каждого записана причина отсечения |
| early\_precision | (👍 + ⭐) / показано в «Ранних сигналах» | ≥ 30% — секция и должна быть шумнее |
| dedup\_errors | ручная выборка 20 историй в неделю | ≤ 1 неверная склейка, ≤ 2 несклеенных дубля |
| llm\_cost | из `runs.stats`, в день | в пределах оценки ниже |

Пороги в `config.py`, стартовые значения и куда двигать:

| Параметр | Старт | Диапазон | Симптом → действие |
| --- | --- | --- | --- |
| CLUSTER\_SIM | 0.80 | 0.76–0.88 | склеивает разное → выше; дубли врозь → ниже. 0.86 подобран без модели; у text-embedding-3-large (1536) дубли из разных источников лежат на 0.80–0.86 (проверка 15.10.2026) |
| SAME\_SOURCE\_SIM | 0.98 | 0.95–0.99 | шаблоны одного канала слипаются → выше |
| URL\_MATCH\_MIN\_SIM | 0.5 | 0.3–0.7 | одна популярная ссылка склеивает разные посты → выше |
| STORY\_MATCH\_DAYS | 7 | 3–14 | старая история «воскресает» → меньше |
| PREFILTER\_N | 60 | 40–120 | missed отсекает prefilter → больше |
| PREFILTER\_MIN\_SIM | 0.30 | 0.20–0.45 | arXiv-шум в кандидатах → выше; missed из arXiv → ниже |
| HEAT\_BYPASS | 3 | 2–5 | дайджест забит мейнстримом → выше |
| CANDIDATE\_WINDOW\_H | 36 | 24–72 | вчерашнее всплывает дважды → меньше |
| TASTE\_NEG\_K | 3 | 2–5 | 👎-центроиды режут лишнее → меньше |
| WEEKLY\_TASTE\_AT | вс 09:00 | `off` — не планируется | — |
| DIGEST\_N | 6 | 5–8 | не дочитываешь → меньше |
| DIGEST\_WAVES | 07:30 / 13:00 / 19:00 | 1–4 волны | волна теряется в ленте → сдвинуть время; мало историй → меньше волн; `[]` — волны на паузе |
| DIGEST\_PREPARE\_AT | 07:00 | за 20–60 мин до первой волны | подготовка не успевает к волне → раньше |
| WEEKLY\_POST\_AT | вс 20:00 | вс, после вечерней волны | `off` — пост не планируется |
| ENRICH\_TEXT\_CHARS | 2000 | 1000–4000 | enrich путает истории без контекста → больше |
| ARTICLE\_SOURCE\_CHARS | 20 000 | 8 000–40 000 | в мини-статье не хватает фактов → больше; дорого → меньше |
| DRAFTS\_POLL\_S | 60 | 30–300 | черновики приходят долго → меньше |
| DRAFTS\_EN | true | true / false | X и LinkedIn не ведутся → false |
| DRAFT\_SOURCE\_CHARS | 6 000 | 3 000–20 000 | в черновике нет конкретной детали → больше |
| EARLY\_N | 3 | 2–5 | секция пустая → больше; шумная → меньше |
| MAX\_PER\_CATEGORY | 2 | 1–3 | одни релизы → меньше |
| W\_LLM / W\_HEAT / W\_SRC | 0.6 / 0.25 / 0.15 | сумма = 1 | популярное вытесняет релевантное → W\_HEAT вниз |

Стоимость, оценка по порядку величины: эмбеддинги \~1000 items в день — центы; enrich + score по 60 историй дешёвой моделью — центы в день; мини-статьи сильной моделью — \~9 историй × (\~6k токенов на вход + \~1k на выход) ≈ $0.2 в день, обзор дня и недели — копейки; черновики по ⭐ — несколько центов за историю. Итого ориентир $5–15 в месяц; реальную цифру покажет `runs.stats` после первой недели.

Правило тюнинга: менять один порог за раз и только по метрике, не по впечатлению от одного утра. Неделя наблюдения на новом значении, потом следующий.
