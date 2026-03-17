"""Base classes and data models for all job platforms."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Optional

from job_applier.ai.answerer import AIAnswerer
from job_applier.browser.session import BrowserSession
from job_applier.config import AppConfig
from job_applier.cv.models import CVProfile


@dataclass
class JobListing:
    url: str
    title: str
    company: str
    platform: str
    job_id: str = ""
    location: str = ""
    description: str = ""
    easy_apply: bool = True


@dataclass
class ApplicationResult:
    job: JobListing
    status: Literal["submitted", "skipped", "error", "already_applied", "dry_run"]
    error_message: str = ""
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def to_log_dict(self) -> dict:
        return {
            "ts": self.timestamp.isoformat(),
            "platform": self.job.platform,
            "job_id": self.job.job_id,
            "title": self.job.title,
            "company": self.job.company,
            "url": self.job.url,
            "status": self.status,
            "error": self.error_message,
        }


@dataclass
class FormField:
    label: str
    field_type: Literal[
        "text", "email", "tel", "number", "textarea",
        "select", "radio", "checkbox", "file", "yesno"
    ]
    selector: str
    options: list[str] = field(default_factory=list)
    is_required: bool = False


class BasePlatform(ABC):
    def __init__(
        self,
        session: BrowserSession,
        answerer: AIAnswerer,
        cv_profile: CVProfile,
        config: AppConfig,
    ):
        self._session = session
        self._answerer = answerer
        self._cv = cv_profile
        self._config = config

    @abstractmethod
    async def search_jobs(self) -> list[JobListing]:
        """Return a list of job listings from this platform."""

    @abstractmethod
    async def apply(self, job: JobListing) -> ApplicationResult:
        """Apply to a single job listing."""

    async def _fill_field(
        self,
        page,
        field: FormField,
        job: JobListing,
    ) -> None:
        """Ask the AI for an answer and fill the form field."""
        from playwright.async_api import TimeoutError as PwTimeout

        # Direct fill for known contact fields
        direct = self._direct_fill_value(field)
        if direct is not None:
            answer = direct
        else:
            answer = self._answerer.answer(
                question=field.label,
                field_type=field.field_type,
                options=field.options if field.options else None,
                job_title=job.title,
                company=job.company,
            )

        if not answer:
            return

        await self._session.short_delay(300, 800)

        try:
            if field.field_type in ("text", "email", "tel", "number", "textarea"):
                locator = page.locator(field.selector)
                await locator.click()
                await locator.fill("")
                await locator.type(answer, delay=80)

            elif field.field_type in ("select",):
                await page.locator(field.selector).select_option(label=answer)

            elif field.field_type == "radio":
                # Find the radio button whose label matches the answer
                best = AIAnswerer._best_match(answer, field.options)
                # Try clicking by value or label text
                radios = page.locator(
                    f'input[type="radio"][name="{field.selector}"]'
                )
                count = await radios.count()
                for i in range(count):
                    radio = radios.nth(i)
                    val = await radio.get_attribute("value") or ""
                    if val.lower() == best.lower():
                        await radio.click()
                        break

            elif field.field_type == "checkbox":
                if answer.lower() == "yes":
                    cb = page.locator(field.selector)
                    if not await cb.is_checked():
                        await cb.click()

            elif field.field_type == "file":
                await page.locator(field.selector).set_input_files(
                    str(self._cv_path())
                )

        except PwTimeout:
            pass  # Field may have been hidden/removed; skip silently

    def _direct_fill_value(self, field: FormField) -> str | None:
        """Return a value from CV data for well-known field types."""
        label = field.label.lower()
        ft = field.field_type

        if ft == "email" or "email" in label:
            return self._cv.email
        if ft == "tel" or any(w in label for w in ("phone", "mobile", "telephone")):
            return self._cv.phone
        if any(w in label for w in ("first name", "firstname")):
            parts = self._cv.full_name.split()
            return parts[0] if parts else ""
        if any(w in label for w in ("last name", "lastname", "surname")):
            parts = self._cv.full_name.split()
            return parts[-1] if len(parts) > 1 else self._cv.full_name
        if "full name" in label or "your name" in label:
            return self._cv.full_name
        if "linkedin" in label:
            return self._cv.linkedin_url
        if "github" in label:
            return self._cv.github_url
        if any(w in label for w in ("city", "location", "address")):
            return self._cv.location

        return None

    def _cv_path(self) -> str:
        import os
        return os.path.expanduser(self._config.cv.path)
