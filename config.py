"""Settings, thresholds and model names. Values come from .env; thresholds start at the spec's «Пороги» table."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore")

    # Telegram user session
    tg_api_id: int
    tg_api_hash: str
    tg_session: str = ""
    notes_channel_id: int | None = None
    digest_channel_id: int | None = None

    # Bot
    bot_token: str = ""
    owner_chat_id: int | None = None

    database_url: str

    # Newsletter mailbox (IMAP adapter): forwarded Medium Daily Digest
    imap_host: str = "imap.gmail.com"
    imap_user: str = ""
    imap_password: str = ""

    # OpenAI
    openai_api_key: str = ""
    embed_model: str = ""
    cheap_model: str = ""
    strong_model: str = ""
    llm_timeout_s: float = 180.0  # the SDK default is 10 min: one stuck call held a whole preview

    # Fetch
    tg_backfill_hours: int = 48
    tg_pause_min_s: float = 0.5
    tg_pause_max_s: float = 1.0
    tg_floodwait_retries: int = 3
    feed_max_age_h: int = 48  # older feed entries and undated ones are not news
    http_timeout_s: float = 30.0
    short_link_timeout_s: float = 5.0

    # Embeddings
    embed_batch: int = 100
    embed_text_chars: int = 1500
    embed_dimensions: int = 1536  # = vector(1536); HNSW in pgvector indexes at most 2000 dims
    embed_usd_per_mtok: float = 0.13  # price of EMBED_MODEL per 1M tokens (text-embedding-3-large), for runs.stats

    # LLM prices, USD per 1M tokens (input, output); developers.openai.com/api/docs/pricing, 2026-10-15
    llm_prices: dict[str, tuple[float, float]] = {
        "gpt-6-luna": (0.10, 0.50),
        "gpt-6.1-sol": (2.00, 10.00),
    }

    # Taste profile
    taste_model: str = ""  # empty → STRONG_MODEL: the profile is built rarely and steers every score
    taste_recent_n: int = 300  # newest notes the profile text is written from
    taste_chunk_n: int = 100  # chunk size if the input is too long for one call
    taste_max_input_chars: int = 200_000
    taste_k: int = 5
    taste_k_large: int = 8  # with 300+ examples
    taste_k_large_from: int = 300

    # Thresholds
    # 0.86 in the spec's table; text-embedding-3-large at 1536 dims puts cross-source duplicates at 0.80–0.86
    # (checked on 2.3k items, 2026-10-15)
    cluster_sim: float = 0.80
    # a story that already holds an item of the same source takes another one only if it is a near-verbatim repeat:
    # one channel's daily templates (horoscopes, vacancies, results) are 0.95+ alike but are different stories
    same_source_sim: float = 0.98
    # an exact shared URL still needs this similarity to glue: a popular repo link alone must not merge unrelated posts
    url_match_min_sim: float = 0.5
    story_match_days: int = 7
    prefilter_n: int = 60
    prefilter_min_sim: float = 0.30
    heat_bypass: int = 3
    candidate_window_h: int = 36
    digest_n: int = 6
    digest_waves: list[str] = ["07:30", "13:00", "19:00"]  # wave 1 = header + top stories, 2 = the rest, 3 = early signals
    digest_tz: str = "Europe/Minsk"
    wave1_n: int = 3
    digest_prepare_at: str = "07:00"  # prefilter → enrich → score → select → mini-articles → overview, before wave 1
    weekly_post_at: str = "sun 20:00"  # «Неделя: куда движемся», after the evening wave; "off" = not scheduled
    # stories the LLM could not score (a failed batch) rank by taste similarity plus this much per extra source
    interim_heat_bonus: float = 0.05
    enrich_batch: int = 10
    score_batch: int = 15
    enrich_text_chars: int = 2000
    article_source_chars: int = 20_000
    draft_source_chars: int = 6_000  # the «best source excerpt» of the draft prompts
    drafts_en: bool = True  # EN drafts for X and LinkedIn next to the RU one
    drafts_poll_s: int = 60  # the worker looks for new ⭐ this often
    drafts_window_h: int = 72  # a ⭐ older than this without drafts is not picked up any more
    stale_after_h: int = 48  # unselected stories older than this → dismissed
    fetch_every_h: int = 2
    early_n: int = 3
    max_per_category: int = 2
    w_llm: float = 0.6
    w_heat: float = 0.25
    w_src: float = 0.15


@lru_cache
def get_settings() -> Settings:
    return Settings()
