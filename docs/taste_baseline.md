# Вкус и источники — базовая линия

Oct 14, 2026 · ручной разбор канала-заметок `@oldgnotes` целиком (2461 сообщение, 2231 item, ноябрь 2020 — октябрь 2026). Нужен как эталон для шага 4 («Bootstrap вкуса»): с ним сверяется профиль, который соберёт LLM, и для шага 2 — какие источники заводить.

## Дрейф интересов

| Период | О чём сохранял |
| --- | --- |
| 2020–2022 | фронтенд: JS, React, Next.js, CSS, собеседования, книги |
| 2023–2024 | LLM внутри Next.js-приложений, локальные модели (Ollama, LM Studio, MLX), RAG по своим документам, OCR, дообучение |
| 2025 | агентный RAG, GraphRAG и графы знаний, MCP-серверы, context engineering, первые месяцы Claude Code, spec-driven (BMAD, Spec Kit) |
| 2026 | экосистема Claude Code, обвязка и память агентов, open-weight модели на своём железе, голос, модели решений |

Доля постов с темой, 2025 → 2026: агенты 25% → 41%, Claude Code и coding-агенты 6% → 24%, память агентов 3% → 14%, RAG 14% → 4%, фронтенд ~3%.

## Ядро

1. Claude Code и coding-агенты на практике: скиллы, сабагенты, хуки, плагины и моды, CLAUDE.md, параллельные агенты, экономия токенов, устройство изнутри (системные промпты, исходники).
2. Обвязка агентов (harness): память, оркестрация, песочницы, почему агенты ломаются, само-улучшение обвязки.
3. MCP: как писать серверы, изменения протокола, как тестировать.
4. Open-weight модели, которые запускаются у себя: Qwen, Gemma, GLM, MiniMax, DeepSeek; требования к памяти, GGUF/MLX, Mac и телефон.
5. Голос локально: TTS, ASR, клонирование, синхронный перевод.
6. Модели решений (Jev и аналоги) — горячая тема с сентября 2026.

Второстепенное: spec-driven разработка, deep research агенты, ИИ в трейдинге и рынках прогнозов, идеи micro-SaaS, качество UI от агентов.

## Сигналы качества

Репозиторий, который можно поставить сегодня (GitHub — 332 ссылки); практика от первого лица; конкретные цифры (во сколько раз дешевле/быстрее, сколько памяти); авторы-практики, а не агрегаторы.

## Что не сохраняет

Крипто- и трейдинг-новости (1–2% при множестве подписок), генерация картинок и видео (~1%), корпоративные анонсы без кода и цифр, фронтенд после 2025 года, вакансии, гороскопы.

## Поведение

- ~70% постов — голая ссылка, в основном Medium (620), часто пачками по 20–35 за день: это список «прочитать потом». Medium — главный источник.
- X: вирусные треды без постоянных авторов. Отдельный X-источник не нужен — хватает разворачивания ссылок через fxtwitter.

## Telegram: настройка источников

Применяется `db/sources_tuning.sql`: выключен шум (прокси, вакансии, гороскопы и таро, прайсы техники, листинги и NFT-анонсы), крипто и трейдинг — вес 0.5, самые пересылаемые в заметки каналы — 1.2: Анализ данных, Machinelearning, Hacker News, Нейронавт, GitHub Community, Data Secrets, Поляков считает, Никита Велс, Глеб Кудрявцев.

## Источники к шагу 2

URL фидов проверяются автообнаружением при добавлении; где RSS нет — `page_watch`.

Блоги лабораторий и вендоров (первоисточники, вес 1.3):

- OpenAI (news, developers), Anthropic (news, engineering), Google DeepMind, Google Research, Google AI for Developers, Meta AI, Microsoft Research, NVIDIA Technical Blog и NVIDIA Research, Mistral, Cohere, xAI, Qwen, Apple Machine Learning Research, Amazon Science, Ai2, Hugging Face blog.
- Changelog / release notes: Anthropic API, Claude Code, OpenAI API, Gemini API.

Релизы на GitHub (`releases.atom`, вес 1.3): claude-code, MCP spec и SDK, FastMCP, OpenAI Agents SDK, LangGraph, ollama, llama.cpp, vLLM, unsloth, Qwen.

Исследования и код (1.2): arXiv cs.AI/cs.CL/cs.LG, HF daily papers, HF trending models.

Люди и сообщества (1.0):

- Medium — теги `claude-code`, `ai-agents`, `mcp`, `rag`, `llm`, `context-engineering`; издания levelup.gitconnected, towardsai, ai.gopubby, generativeai.pub, ai.plainenglish.
- Reddit r/LocalLLaMA, r/ClaudeAI.
- Блоги: Simon Willison, Latent Space, Interconnects, Ahead of AI, Lilian Weng, Chip Huyen.
- Hacker News — уже идёт через Telegram-канал, адаптер `hn` добавит score и обсуждение.
