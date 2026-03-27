"""Glassdoor job search and apply automation."""
from __future__ import annotations

import logging
from urllib.parse import urlencode

from job_applier.platforms.base import (
    ApplicationResult,
    BasePlatform,
    JobListing,
)
from job_applier.utils.errors import BotDetectionError

logger = logging.getLogger("job_applier.platforms.glassdoor")

_SEARCH_BASE = "https://www.glassdoor.com/Job/jobs.htm?"


class GlassdoorPlatform(BasePlatform):

    async def search_jobs(self) -> list[JobListing]:
        page = await self._session.new_page()
        listings: list[JobListing] = []

        try:
            for title in self._config.search.job_titles:
                for location in self._config.search.locations:
                    results = await self._search_page(page, title, location)
                    listings.extend(results)
        finally:
            await page.close()

        seen = set()
        unique = []
        for j in listings:
            if j.job_id not in seen:
                seen.add(j.job_id)
                unique.append(j)
        return unique[: self._config.platforms.glassdoor.max_per_run]

    async def _search_page(
        self, page, title: str, location: str
    ) -> list[JobListing]:
        params = {"sc.keyword": title, "locT": "C", "locName": location}
        url = _SEARCH_BASE + urlencode(params)
        await page.goto(url, timeout=30000, wait_until="domcontentloaded")
        await self._session.short_delay(2000, 3000)

        # Dismiss cookie consent if present
        consent_btn = page.locator(
            'button:has-text("Accept"), button[id*="onetrust-accept"]'
        ).first
        if await consent_btn.count():
            try:
                await consent_btn.click()
                await self._session.short_delay(500, 1000)
            except Exception:
                pass

        if "captcha" in page.url.lower():
            raise BotDetectionError("Glassdoor bot detection triggered")

        listings: list[JobListing] = []
        cards = await page.locator('[data-test="job-list-item"], li[data-id]').all()

        for card in cards[:20]:
            try:
                job_id = await card.get_attribute("data-id") or ""

                title_el = card.locator('[data-test="job-link"], .jobLink, a.job-search-card__title')
                company_el = card.locator(
                    '.EmployerProfile_compactEmployerName__9MGcV,'
                    '[data-test="employer-name"],.employer-name'
                )

                job_title = (await title_el.inner_text()).strip() if await title_el.count() else title
                company = (await company_el.inner_text()).strip() if await company_el.count() else ""
                href = await title_el.get_attribute("href") if await title_el.count() else ""
                if href and not href.startswith("http"):
                    href = "https://www.glassdoor.com" + href

                if job_id and href:
                    listings.append(JobListing(
                        url=href,
                        title=job_title,
                        company=company,
                        platform="glassdoor",
                        job_id=f"glassdoor:{job_id}",
                    ))
            except Exception:
                continue

        return listings

    async def apply(self, job: JobListing) -> ApplicationResult:
        if self._config.behavior.dry_run:
            logger.info("[DRY RUN] Would apply to Glassdoor: %s @ %s", job.title, job.company)
            return ApplicationResult(job=job, status="dry_run")

        page = await self._session.new_page()
        try:
            await page.goto(job.url, timeout=30000, wait_until="domcontentloaded")
            await self._session.short_delay(1500, 2500)

            # Try Easy Apply button first
            easy_apply = page.locator(
                'button:has-text("Easy Apply"), button[data-test="easyApply"]'
            ).first

            if await easy_apply.count() and await easy_apply.is_visible():
                await easy_apply.click()
                await self._session.short_delay(1500, 2000)
                # Glassdoor Easy Apply uses a modal similar to LinkedIn
                # Delegate to generic form handler on the modal
                from job_applier.platforms.generic import GenericPlatform
                generic = GenericPlatform(
                    self._session, self._answerer, self._cv, self._config
                )
                ok = await generic._detect_and_fill_form(page, job)
                return ApplicationResult(
                    job=job,
                    status="submitted" if ok else "error",
                    error_message="" if ok else "Glassdoor modal fill failed",
                )

            # External "Apply on company site" button
            external_btn = page.locator(
                'a:has-text("Apply on company site"), button:has-text("Apply on company site")'
            ).first
            if await external_btn.count():
                href = await external_btn.get_attribute("href") or ""
                if href:
                    logger.info(
                        "Glassdoor %s → external ATS: %s", job.job_id, href
                    )
                    from job_applier.platforms.generic import GenericPlatform
                    generic = GenericPlatform(
                        self._session, self._answerer, self._cv, self._config
                    )
                    ext_job = JobListing(
                        url=href, title=job.title, company=job.company,
                        platform="generic", job_id=job.job_id
                    )
                    result = await generic.apply(ext_job)
                    return ApplicationResult(
                        job=job,
                        status=result.status,
                        error_message=result.error_message,
                    )

            return ApplicationResult(
                job=job, status="skipped",
                error_message="No apply button found on Glassdoor listing"
            )

        except BotDetectionError as e:
            return ApplicationResult(job=job, status="error", error_message=str(e))
        except Exception as e:
            logger.exception("Glassdoor apply error for %s: %s", job.url, e)
            return ApplicationResult(job=job, status="error", error_message=str(e))
        finally:
            await page.close()
