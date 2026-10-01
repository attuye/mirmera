from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    database_url: str = "sqlite:///./mirmera.db"

    # Which provider backs AI features: mock | claude | openai | local
    # "mock" needs no API key or billing; switch in backend/.env when ready.
    default_ai_provider: str = "mock"

    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-5-5"

    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"
    openai_base_url: str | None = None  # e.g. http://localhost:1234/v1 for LM Studio

    local_model_base_url: str = "http://localhost:11434"
    local_model_name: str = "qwen3:8b"


settings = Settings()