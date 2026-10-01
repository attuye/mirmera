from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    database_url: str = "sqlite:///./mirmera.db"

    # Frontend origins allowed to call the API, comma-separated.
    cors_origins: str = "http://localhost:5173"

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

    # Browser session runner (Playwright). Headed by default: the tester tests in
    # that window. Headless is for automated tests and CI.
    runner_headless: bool = False
    runner_chromium_path: str | None = None  # use a specific Chromium instead of Playwright's
    # Where screenshots, traces and HAR files are kept, one folder per session.
    # They can contain whatever the tested app shows, so keep them out of git.
    evidence_dir: str = "./evidence"


settings = Settings()