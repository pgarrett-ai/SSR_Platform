"""Environment-driven settings. One source of truth for all three modules, read from `.env`.

Union of capstack's and hazard's settings (fulcrum's engine has none). The `.env` lives at
the platform root; the legacy capstack/.env is read first as a fallback so an existing setup
keeps working, with platform-level files taking precedence.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent   # .../platform/backend
REPO_ROOT = BACKEND_DIR.parent                                 # .../platform
LEGACY_CAPSTACK_ENV = REPO_ROOT.parent / "capstack" / ".env"   # pre-merge fallback
DATA_DIR = BACKEND_DIR / "app" / "data"
CACHE_DIR = BACKEND_DIR / "app" / "cache"
FILINGS_CACHE_DIR = BACKEND_DIR / ".edgar_cache"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # later files take precedence: platform .env overrides the legacy capstack one
        env_file=(LEGACY_CAPSTACK_ENV, REPO_ROOT / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # EDGAR demands a descriptive User-Agent (name + email) or it returns 403 and rate-limits.
    sec_user_agent: str = "Distressed Debt Research example@example.com"

    # LLM extraction (covenant + OBS footnotes). Empty string => LLM phases are skipped gracefully.
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-4-8"

    database_url: str = f"sqlite:///{(DATA_DIR / 'platform.db').as_posix()}"

    hero_tickers: str = "AAL,ATUS,TSE"

    # PR-6 auth v1 (plan §11): shared bearer token for every /api/* route except /api/health.
    # Empty (the localhost default) = open — set it the day the app leaves localhost.
    platform_api_token: str = ""

    # Politeness: edgartools/EDGAR allow ~10 req/s; we stay well under.
    edgar_max_requests_per_sec: float = 8.0

    # --- hazard (default-risk) settings ---
    # FINRA TRACE bond spreads (optional). Empty => the bond-spread section is skipped.
    finra_api_key: str = ""
    finra_api_secret: str = ""
    market_index: str = "SPY"        # benchmark for excess return / CHS relative size
    risk_free_rate: float = 0.04     # Merton r; refine from the curve later

    @property
    def hero_ticker_set(self) -> set[str]:
        return {t.strip().upper() for t in self.hero_tickers.split(",") if t.strip()}

    @property
    def llm_key_set(self) -> bool:
        return bool(self.anthropic_api_key.strip())

    @property
    def llm_enabled(self) -> bool:
        return self.llm_key_set and _runtime().get("llm_enabled", True)

    @property
    def trace_enabled(self) -> bool:
        return bool(self.finra_api_key.strip() and self.finra_api_secret.strip())


# Runtime toggles the UI can flip without a restart. Read per access (tiny file) so the
# lru_cached Settings needs no invalidation.
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
    FILINGS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return Settings()
