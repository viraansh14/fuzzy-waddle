from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, field_validator


class AnthropicConfig(BaseModel):
    api_key: str = ""
    model: str = "claude-opus-4-6"
    max_tokens: int = 512


class CVConfig(BaseModel):
    path: str


class SearchConfig(BaseModel):
    job_titles: list[str] = []
    locations: list[str] = ["Remote"]
    keywords: list[str] = []
    exclude_keywords: list[str] = []
    salary_min: int = 0
    max_applications_per_run: int = 10
    blacklist_companies: list[str] = []


class LinkedInPlatformConfig(BaseModel):
    enabled: bool = True
    easy_apply_only: bool = True
    max_per_run: int = 10


class IndeedPlatformConfig(BaseModel):
    enabled: bool = True
    quick_apply_only: bool = True
    max_per_run: int = 10


class GlassdoorPlatformConfig(BaseModel):
    enabled: bool = False
    max_per_run: int = 5


class GenericUrlsConfig(BaseModel):
    enabled: bool = False
    urls: list[str] = []


class PlatformsConfig(BaseModel):
    linkedin: LinkedInPlatformConfig = LinkedInPlatformConfig()
    indeed: IndeedPlatformConfig = IndeedPlatformConfig()
    glassdoor: GlassdoorPlatformConfig = GlassdoorPlatformConfig()
    generic_urls: GenericUrlsConfig = GenericUrlsConfig()


class BrowserConfig(BaseModel):
    headless: bool = False
    slow_mo_ms: int = 200
    viewport_width: int = 1280
    viewport_height: int = 900


class BehaviorConfig(BaseModel):
    min_delay_between_apps_s: int = 30
    max_delay_between_apps_s: int = 90
    dry_run: bool = False
    state_file: str = "./data/applied_jobs.json"


class AuthConfig(BaseModel):
    cookie_dir: str = "./data/cookies"


class LoggingConfig(BaseModel):
    level: str = "INFO"
    log_file: str = "./data/logs/applications.jsonl"


class AppConfig(BaseModel):
    anthropic: AnthropicConfig = AnthropicConfig()
    cv: CVConfig
    search: SearchConfig = SearchConfig()
    platforms: PlatformsConfig = PlatformsConfig()
    browser: BrowserConfig = BrowserConfig()
    behavior: BehaviorConfig = BehaviorConfig()
    auth: AuthConfig = AuthConfig()
    logging: LoggingConfig = LoggingConfig()

    @field_validator("anthropic", mode="before")
    @classmethod
    def resolve_api_key(cls, v):
        if isinstance(v, dict):
            key = v.get("api_key", "")
            v["api_key"] = _resolve_env(str(key))
        return v


def _resolve_env(value: str) -> str:
    """Replace ${VAR_NAME} with environment variable values."""
    def replacer(match: re.Match) -> str:
        var = match.group(1)
        return os.environ.get(var, match.group(0))
    return re.sub(r"\$\{([^}]+)\}", replacer, value)


def load_config(path: str | Path) -> AppConfig:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"Config file not found: {path}\n"
            "Copy config.example.yaml to config.yaml and fill in your details."
        )
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return AppConfig.model_validate(raw or {})
