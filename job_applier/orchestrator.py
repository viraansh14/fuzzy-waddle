"""Main orchestration loop: coordinates CV parsing, auth, and job applications."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
from pathlib import Path
from typing import Optional

import anthropic

from job_applier.ai.answerer import AIAnswerer
from job_applier.browser.auth import AuthManager
from job_applier.browser.session import BrowserSession
from job_applier.config import AppConfig
from job_applier.cv.extractor import CVExtractor
from job_applier.cv.models import CVProfile
from job_applier.cv.parser import extract_text
from job_applier.platforms.base import ApplicationResult, JobListing
from job_applier.utils.errors import AuthenticationError, CVParseError

logger = logging.getLogger("job_applier.orchestrator")


class Orchestrator:
    def __init__(self, config: AppConfig):
        self._config = config
        self._applied_ids: set[str] = set()

    async def run(self) -> dict:
        cfg = self._config

        # 1. Parse CV
        logger.info("Parsing CV: %s", cfg.cv.path)
        raw_text = extract_text(cfg.cv.path)
        logger.info("Extracted %d chars from CV", len(raw_text))

        # 2. Extract structured data
        api_key = cfg.anthropic.api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        client = anthropic.Anthropic(api_key=api_key)
        extractor = CVExtractor(client, cfg.anthropic.model)
        cv_profile = extractor.extract(raw_text)
        logger.info("CV extracted: %s <%s>", cv_profile.full_name, cv_profile.email)

        # 3. Load applied jobs state
        self._applied_ids = self._load_state()

        # 4. Set up answerer
        answerer = AIAnswerer(client, cfg.anthropic.model, cv_profile, cfg.anthropic.max_tokens)

        # 5. Set up auth
        auth = AuthManager(cfg.auth.cookie_dir)

        stats = {"submitted": 0, "skipped": 0, "error": 0, "dry_run": 0}
        results: list[ApplicationResult] = []

        async with BrowserSession(cfg.browser, cfg.behavior) as session:
            # 6. Run each enabled platform
            platforms = self._build_platforms(session, answerer, cv_profile)

            for platform_name, platform in platforms:
                # Authenticate
                try:
                    await auth.ensure_logged_in(platform_name, session)
                except AuthenticationError as e:
                    logger.error("Auth failed for %s: %s — skipping", platform_name, e)
                    continue

                # Search for jobs
                logger.info("Searching jobs on %s…", platform_name)
                try:
                    listings = await platform.search_jobs()
                except Exception as e:
                    logger.error("Search failed on %s: %s", platform_name, e)
                    continue

                listings = self._filter_listings(listings)
                logger.info(
                    "Found %d jobs on %s after filtering", len(listings), platform_name
                )

                # Apply to each
                total_this_run = 0
                for job in listings:
                    if total_this_run >= cfg.search.max_applications_per_run:
                        logger.info("Reached max_applications_per_run limit")
                        break
                    if job.job_id in self._applied_ids:
                        logger.debug("Already applied to %s — skipping", job.job_id)
                        stats["skipped"] += 1
                        continue

                    logger.info(
                        "Applying to: %s @ %s (%s)", job.title, job.company, platform_name
                    )
                    result = await platform.apply(job)
                    results.append(result)
                    stats[result.status] = stats.get(result.status, 0) + 1
                    self._log_result(result)

                    if result.status in ("submitted", "dry_run"):
                        self._applied_ids.add(job.job_id)
                        total_this_run += 1

                    # Delay between applications
                    if total_this_run < len(listings):
                        delay = random.uniform(
                            cfg.behavior.min_delay_between_apps_s,
                            cfg.behavior.max_delay_between_apps_s,
                        )
                        logger.debug("Waiting %.1fs before next application", delay)
                        await asyncio.sleep(delay)

        # 7. Save state
        self._save_state()

        # 8. Print summary
        print(
            f"\n{'='*50}\n"
            f"Run complete: "
            f"{stats.get('submitted', 0)} submitted, "
            f"{stats.get('dry_run', 0)} dry-run, "
            f"{stats.get('skipped', 0)} skipped, "
            f"{stats.get('error', 0)} errors\n"
            f"{'='*50}"
        )
        return stats

    def _build_platforms(
        self, session: BrowserSession, answerer: AIAnswerer, cv_profile: CVProfile
    ) -> list[tuple[str, object]]:
        from job_applier.platforms.generic import GenericPlatform
        from job_applier.platforms.glassdoor import GlassdoorPlatform
        from job_applier.platforms.indeed import IndeedPlatform
        from job_applier.platforms.linkedin import LinkedInPlatform

        cfg = self._config
        result = []
        if cfg.platforms.linkedin.enabled:
            result.append(("linkedin", LinkedInPlatform(session, answerer, cv_profile, cfg)))
        if cfg.platforms.indeed.enabled:
            result.append(("indeed", IndeedPlatform(session, answerer, cv_profile, cfg)))
        if cfg.platforms.glassdoor.enabled:
            result.append(("glassdoor", GlassdoorPlatform(session, answerer, cv_profile, cfg)))
        if cfg.platforms.generic_urls.enabled:
            result.append(("generic", GenericPlatform(session, answerer, cv_profile, cfg)))
        return result

    def _filter_listings(self, listings: list[JobListing]) -> list[JobListing]:
        cfg = self._config
        filtered = []
        blacklist = [c.lower() for c in cfg.search.blacklist_companies]
        exclude_kw = [k.lower() for k in cfg.search.exclude_keywords]

        for job in listings:
            if job.job_id in self._applied_ids:
                continue
            if job.company.lower() in blacklist:
                continue
            desc_lower = (job.title + " " + job.description).lower()
            if any(kw in desc_lower for kw in exclude_kw):
                continue
            filtered.append(job)

        return filtered

    def _load_state(self) -> set[str]:
        path = Path(self._config.behavior.state_file)
        if not path.exists():
            return set()
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return set(data.get("applied_ids", []))
        except Exception:
            return set()

    def _save_state(self) -> None:
        path = Path(self._config.behavior.state_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"applied_ids": sorted(self._applied_ids)}, f, indent=2)
        tmp.replace(path)

    def _log_result(self, result: ApplicationResult) -> None:
        import json as _json
        log_path = Path(self._config.logging.log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(_json.dumps(result.to_log_dict()) + "\n")
        level = logging.INFO if result.status == "submitted" else logging.DEBUG
        logger.log(
            level,
            "[%s] %s @ %s — %s",
            result.status.upper(),
            result.job.title,
            result.job.company,
            result.error_message or "ok",
        )
