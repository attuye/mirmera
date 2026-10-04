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
    # Let thinking models (qwen3...) reason before answering: slower, and replies can come back empty.
    local_model_think: bool = False

    # Browser session runner (Playwright). Headed by default: the tester tests in
    # that window. Headless is for automated tests and CI.
    runner_headless: bool = False
    runner_chromium_path: str | None = None  # use a specific Chromium instead of Playwright's
    # Extra signals from the recorded browser (evidence, not verdicts):
    runner_accessibility_checks: bool = True  # check each page when it loads (labels, alt text, contrast...)
    slow_request_ms: int = 2000  # an API call (fetch/XHR) slower than this is recorded
    slow_page_ms: int = 3000  # a page that takes longer than this to load is recorded
    # Where screenshots, traces and HAR files are kept, one folder per session.
    # They can contain whatever the tested app shows, so keep them out of git.
    evidence_dir: str = "./evidence"

    # Issue trackers. Secrets live here only; which GitHub repo / Jira project
    # each Mirmera project files to is set in the app (project settings).
    github_token: str | None = None  # fine-grained token with Issues: read and write
    github_api_url: str = "https://api.github.com"
    github_labels: str = ""  # comma-separated labels to add, e.g. "bug,from-mirmera" (must exist in the repo)
    jira_base_url: str | None = None  # e.g. https://addisverify.atlassian.net
    jira_email: str | None = None
    jira_api_token: str | None = None
    jira_issue_type: str = "Bug"
    # Fake GitHub and Jira kept in memory: for tests, demos and trying the flow.
    mock_trackers: bool = False
    # Check filed issues every N minutes and retest the ones newly closed (0: only on the button).
    retest_poll_minutes: int = 0
    # Where testers open Mirmera; when set, filed issues link back to the bug.
    public_app_url: str | None = None


settings = Settings()