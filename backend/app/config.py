from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite:///./explore.db"
    # "mock" needs no API key or billing — swap to "claude" once billing is set up
    # (backend/.env or per-request override), nothing else in the app changes.
    default_ai_provider: str = "mock"
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    local_model_base_url: str = "http://localhost:11434"
    local_model_name: str = "qwen3:8b"

    class Config:
        env_file = ".env"


settings = Settings()
