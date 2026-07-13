from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Native dev runs from backend/ with .env at the repo root; containers get
    # everything as real env vars (which always take precedence over dotenv).
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_auth_token: str = ""
    base_currency: str = "USD"
    tz: str = "America/Mexico_City"
    frontend_origin: str = "http://localhost:5173"

    database_url: str = "postgresql://plutus:plutus@localhost:5432/plutus"
    redis_url: str = "redis://localhost:6379/0"

    provider_stocks: str = "tiingo"
    provider_crypto: str = "binance"
    provider_forex: str = "twelvedata"
    provider_fundamentals: str = "fmp"
    provider_news: str = "finnhub"

    tiingo_api_key: str = ""
    coingecko_api_key: str = ""
    twelvedata_api_key: str = ""
    finnhub_api_key: str = ""
    fmp_api_key: str = ""

    # benchmarks for relative strength (spec §5.3); resolved by symbol across classes
    benchmark_stock: str = "SPY"
    benchmark_crypto: str = "BTC"
    benchmark_forex: str = "UUP"  # DXY is paid-gated on Twelve Data free (verified 404)

    # ~5y: leaves a usable backtest window after the 300-bar indicator warmup.
    # Tiingo/Twelve Data serve the whole window in one request; Binance paginates.
    initial_backfill_days: int = 1825

    # quantstats HTML reports land here; compose mounts a shared volume on app+worker
    artifacts_dir: str = "./artifacts"

    # --- Alerts (spec §6.5) — both channels optional; unconfigured channels are skipped.
    # email is configured iff smtp_host + alert_email_to are set;
    # telegram iff bot token + chat id are set.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_pass: str = ""
    alert_email_to: str = ""
    alert_email_from: str = ""  # falls back to smtp_user

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # remind this many days before a fixed-term bank investment matures (spec §7.4)
    maturity_reminder_days: int = 7

    # an fx close older than this converts with a stale warning instead of silently
    fx_max_stale_days: int = 7

    # app <-> agent-sidecar shared secret (phase 9 M2). The sidecar refuses to
    # boot without it and 401s any request that doesn't present it.
    sidecar_shared_secret: str = ""

    # --- AI agent layer (spec §13) — env values are the seed; app_settings rows
    # (Settings UI) override them so the provider can switch without a restart.
    llm_provider: str = "claude-subscription"
    llm_model: str = ""  # provider-specific model id override
    # the Node sidecar wrapping the claude agent SDK (compose: http://agent-sidecar:8787).
    # CLAUDE_CODE_OAUTH_TOKEN is consumed by the sidecar container only, never here.
    claude_sidecar_url: str = "http://localhost:8787"
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    google_api_key: str = ""
    openrouter_api_key: str = ""
    ollama_base_url: str = "http://localhost:11434"
    # hard daily cap across chat + research tasks + translations (spec §13.4)
    agent_daily_token_budget: int = 500_000
    agent_max_tool_iterations: int = 15
    agent_nightly_memo_limit: int = 3
    mcp_tool_tier: str = "write"  # read = queries only over MCP
    # encrypts secret app_settings values (API keys) at rest
    fernet_key: str = ""

    @property
    def sqlalchemy_url(self) -> str:
        url = self.database_url
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+psycopg://", 1)
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()
