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

    # OpenAI
    openai_api_key: str = ""
    embed_model: str = ""
    cheap_model: str = ""
    strong_model: str = ""

    # Fetch
    tg_backfill_hours: int = 48
    tg_pause_min_s: float = 0.5
    tg_pause_max_s: float = 1.0
    tg_floodwait_retries: int = 3
    feed_max_age_h: int = 48  # older feed entries and undated ones are not news
    http_timeout_s: float = 30.0
    short_link_timeout_s: float = 5.0

    # Thresholds
    cluster_sim: float = 0.86
    story_match_days: int = 7
    prefilter_n: int = 60
    prefilter_min_sim: float = 0.30
    heat_bypass: int = 3
    candidate_window_h: int = 36
    digest_n: int = 6
    digest_waves: list[str] = ["07:30", "13:00", "19:00"]  # Minsk time; wave 1 = header + top 3
    early_n: int = 3
    max_per_category: int = 2
    w_llm: float = 0.6
    w_heat: float = 0.25
    w_src: float = 0.15


@lru_cache
def get_settings() -> Settings:
    return Settings()
