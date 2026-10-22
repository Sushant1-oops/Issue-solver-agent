"""Central configuration loaded from backend/.env."""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _models(value: str) -> list[str]:
    """Split a comma-separated model list, dropping blanks and duplicates."""
    out: list[str] = []
    for part in (p.strip() for p in value.split(",")):
        if part and part not in out:
            out.append(part)
    return out


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- LLM providers ---
    # Each provider takes a comma-separated list of models tried in order: free
    # tiers retire model names (404) and shed traffic per model (503), so one bad
    # string must not be able to fail a run.
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    primary_model: str = "minimax/minimax-m3.1-flash:free"
    primary_max_tokens: int = 8192

    gemini_api_key: str = ""
    gemini_model: str = "gemini-flash-lite-latest,gemini-2.5-flash"
    gemini_max_tokens: int = 8192

    # --- GitHub ---
    # Fine-grained PAT: reads issues and pushes to repositories you own.
    github_token: str = ""
    # Classic PAT with the `repo` scope: the only kind that can fork someone else's
    # repository, so it is preferred whenever it is set.
    github_classic_token: str = ""
    # Contribute to repos you cannot push to by forking them first.
    allow_fork: bool = True
    # Testing knob: take the fork route even when the repo could be pushed to
    # directly (used to demonstrate cross-repo PRs against your own repository).
    prefer_fork: bool = False

    # --- Execution budgets (seconds) ---
    overall_timeout_s: int = 240
    clone_timeout_s: int = 90
    install_timeout_s: int = 60
    test_timeout_s: int = 75
    llm_timeout_s: int = 120
    fork_wait_timeout_s: int = 60

    # --- Context budgets (characters) ---
    max_file_chars: int = 14000
    max_files_read: int = 3
    repo_map_max_entries: int = 300
    issue_body_max_chars: int = 3500

    # --- Retry policy ---
    max_apply_attempts: int = 2
    max_test_retries: int = 1

    # --- Workspace ---
    workspace_dir: str = "workspace"

    @property
    def token(self) -> str:
        """The token used for every read and write: classic wins because it can fork."""
        return self.github_classic_token or self.github_token

    @property
    def primary_models(self) -> list[str]:
        return _models(self.primary_model)

    @property
    def gemini_models(self) -> list[str]:
        return _models(self.gemini_model)

    @property
    def tokens(self) -> list[str]:
        """Every configured secret, for redacting command output."""
        return [t for t in (self.github_classic_token, self.github_token) if t]

    @property
    def workspace_path(self) -> Path:
        p = Path(self.workspace_dir)
        if not p.is_absolute():
            p = BACKEND_DIR / p
        p.mkdir(parents=True, exist_ok=True)
        return p


settings = Settings()
