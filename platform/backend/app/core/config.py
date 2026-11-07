"""Environment-driven settings, read from platform/.env (backend/.env overrides it)."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent   # .../platform/backend
PLATFORM_DIR = BACKEND_DIR.parent                              # .../platform
DATA_DIR = BACKEND_DIR / "app" / "data"
CACHE_DIR = BACKEND_DIR / "app" / "cache"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(PLATFORM_DIR / ".env", BACKEND_DIR / ".env"),   # later files win
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # EDGAR demands a descriptive User-Agent (name + email) or it returns 403 and rate-limits.
    sec_user_agent: str = "Distressed Debt Research example@example.com"

    # LLM extraction (covenant + OBS footnotes). Empty string => LLM phases are skipped gracefully.
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5-5"

    database_url: str = f"sqlite:///{(DATA_DIR / 'platform.db').as_posix()}"

    hero_tickers: str = "AAL,ATUS,TSE,LCID"

    # Shared bearer token for every /api/* route except /api/health. Empty (the localhost
    # default) = open — set it the day the app leaves localhost.
    platform_api_token: str = ""

    # paced_get's rate, per process. SEC allows 10 req/s per client and every process paces
    # itself: the API runs edgartools at 4 req/s (edgar/client.py) plus this 1. The worker
    # only uses paced_get, so it can run at EDGAR_MAX_REQUESTS_PER_SEC=3 and keep the two
    # processes at 8 together.
    edgar_max_requests_per_sec: float = 1.0

    # --- hazard (default-risk) settings ---
    market_index: str = "SPY"        # benchmark for the 1-year excess return
    risk_free_rate: float = 0.04     # Merton r

    @property
    def hero_ticker_set(self) -> set[str]:
        return {t.strip().upper() for t in self.hero_tickers.split(",") if t.strip()}

    @property
    def llm_key_set(self) -> bool:
        return bool(self.anthropic_api_key.strip())

    @property
    def llm_enabled(self) -> bool:
        return self.llm_key_set and _runtime().get("llm_enabled", True)



# Runtime toggles set through the API (POST /api/settings/llm) without a restart. Read per
# access (tiny file) so the lru_cached Settings needs no invalidation.
RUNTIME_SETTINGS_PATH = DATA_DIR / "runtime_settings.json"


def _runtime() -> dict:
    try:
        return json.loads(RUNTIME_SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def set_llm_runtime_enabled(enabled: bool) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    RUNTIME_SETTINGS_PATH.write_text(
        json.dumps({**_runtime(), "llm_enabled": bool(enabled)}), encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return Settings()
