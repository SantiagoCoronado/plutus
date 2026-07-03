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
    provider_crypto: str = "coingecko"
    provider_forex: str = "twelvedata"

    tiingo_api_key: str = ""
    coingecko_api_key: str = ""
    twelvedata_api_key: str = ""
    finnhub_api_key: str = ""

    initial_backfill_days: int = 730

    @property
    def sqlalchemy_url(self) -> str:
        url = self.database_url
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+psycopg://", 1)
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()
