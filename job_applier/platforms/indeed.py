"""Indeed Quick Apply automation."""
from __future__ import annotations

import logging
from urllib.parse import urlencode

from job_applier.platforms.base import (
    ApplicationResult,
    BasePlatform,
    FormField,
    JobListing,
)
from job_applier.utils.errors import BotDetectionError

logger = logging.getLogger("job_applier.platforms.indeed")

_SEARCH_BASE = "https://www.indeed.com/jobs?"


class IndeedPlatform(BasePlatform):

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
        return unique[: self._config.platforms.indeed.max_per_run]

    async def _search_page(
        self, page, title: str, location: str
    ) -> list[JobListing]:
        params = {
            "q": title,
            "l": location,
            "iafilter": "1",  # Easily apply filter
            "sort": "date",
        }
        url = _SEARCH_BASE + urlencode(params)
        await page.goto(url, timeout=30000, wait_until="domcontentloaded")
        await self._session.short_delay(2000, 3000)

        if "captcha" in page.url.lower() or "challenge" in page.url.lower():
            raise BotDetectionError("Indeed bot detection triggered")

        listings: list[JobListing] = []
        cards = await page.locator(
            'div[data-jk], li.css-5lfssm, .jobsearch-SerpJobCard'
        ).all()

        for card in cards[:20]:
            try:
                job_id = await card.get_attribute("data-jk") or ""
                title_el = card.locator("h2.jobTitle a, a.jcs-JobTitle")
                company_el = card.locator(".companyName, [data-testid='company-name']")

                job_title = (await title_el.inner_text()).strip() if await title_el.count() else title
                company = (await company_el.inner_text()).strip() if await company_el.count() else ""

                if job_id:
                    job_url = f"https://www.indeed.com/viewjob?jk={job_id}"
                    listings.append(JobListing(
                        url=job_url,
                        title=job_title,
                        company=company,
                        platform="indeed",
                        job_id=f"indeed:{job_id}",
                        easy_apply=True,
                    ))
            except Exception:
                continue

        return listings

    async def apply(self, job: JobListing) -> ApplicationResult:
        if self._config.behavior.dry_run:
            logger.info("[DRY RUN] Would apply to Indeed: %s @ %s", job.title, job.company)
            return ApplicationResult(job=job, status="dry_run")

        page = await self._session.new_page()
        try:
            await page.goto(job.url, timeout=30000, wait_until="domcontentloaded")
            await self._session.short_delay(1500, 2500)

            if "captcha" in page.url.lower():
                raise BotDetectionError("Indeed CAPTCHA detected")

            # Click Apply button
            apply_btn = page.locator(
                'button[id="indeedApplyButton"],'
                'a[id="applyButtonLinkContainer"],'
                'button:has-text("Apply now"),'
                'a:has-text("Apply now")'
            ).first

            if not await apply_btn.count():
                return ApplicationResult(
                    job=job, status="skipped",
                    error_message="No Apply button found"
                )

            await apply_btn.click()
            await self._session.short_delay(2000, 3000)

            # Check if redirected to external site
            if "indeed.com" not in page.url and "indeedapply" not in page.url:
                logger.info(
                    "Indeed job %s redirected to external ATS: %s",
                    job.job_id, page.url
                )
                # Hand off to generic handler
                from job_applier.platforms.generic import GenericPlatform
                generic = GenericPlatform(
                    self._session, self._answerer, self._cv, self._config
                )
                ext_job = JobListing(
                    url=page.url, title=job.title, company=job.company,
                    platform="generic", job_id=job.job_id
                )
                result = await generic._detect_and_fill_form(page, ext_job)
                return ApplicationResult(
                    job=job,
                    status="submitted" if result else "error",
                    error_message="" if result else "Generic form fill failed",
                )

            await self._handle_apply_flow(page, job)
            return ApplicationResult(job=job, status="submitted")

        except BotDetectionError as e:
            return ApplicationResult(job=job, status="error", error_message=str(e))
        except Exception as e:
            logger.exception("Indeed apply error for %s: %s", job.url, e)
            return ApplicationResult(job=job, status="error", error_message=str(e))
        finally:
            await page.close()

    async def _handle_apply_flow(self, page, job: JobListing) -> None:
        max_steps = 8
        for step in range(max_steps):
            await self._session.short_delay(1000, 1500)

            # Check for final submit button
            submit_btn = page.locator(
                'button[aria-label="Submit your application"],'
                'button:has-text("Submit your application"),'
                'button:has-text("Submit application")'
            ).first
            if await submit_btn.count() and await submit_btn.is_visible():
                await submit_btn.click()
                logger.info("Submitted Indeed application: %s @ %s", job.title, job.company)
                return

            # Fill current page fields
            await self._fill_apply_page(page, job)
            await self._session.short_delay(800, 1200)

            # Advance to next step
            next_btn = page.locator(
                'button[aria-label="Continue"],'
                'button:has-text("Continue"),'
                'button:has-text("Next")'
            ).first
            if await next_btn.count() and await next_btn.is_visible():
                await next_btn.click()
            else:
                logger.warning("No next button found at step %d for %s", step + 1, job.url)
                return

    async def _fill_apply_page(self, page, job: JobListing) -> None:
        # File upload
        file_inputs = await page.locator('input[type="file"]').all()
        for fi in file_inputs:
            try:
                await fi.set_input_files(self._cv_path())
            except Exception:
                pass

        # Text inputs
        inputs = await page.locator(
            'input[type="text"], input[type="email"], input[type="tel"], input[type="number"]'
        ).all()
        for inp in inputs:
            try:
                current = await inp.input_value()
                if current.strip():
                    continue
                label = await self._get_label(page, inp)
                el_id = await inp.get_attribute("id") or ""
                sel = f"#{el_id}" if el_id else f'[name="{await inp.get_attribute("name")}"]'
                itype = await inp.get_attribute("type") or "text"
                field = FormField(label=label, field_type=itype, selector=sel)
                await self._fill_field(page, field, job)
            except Exception:
                continue

        # Textareas
        textareas = await page.locator("textarea").all()
        for ta in textareas:
            try:
                current = await ta.input_value()
                if current.strip():
                    continue
                label = await self._get_label(page, ta)
                el_id = await ta.get_attribute("id") or ""
                sel = f"#{el_id}" if el_id else "textarea"
                field = FormField(label=label, field_type="textarea", selector=sel)
                await self._fill_field(page, field, job)
            except Exception:
                continue

        # Selects
        selects = await page.locator("select").all()
        for sel_el in selects:
            try:
                label = await self._get_label(page, sel_el)
                options = [
                    (await o.inner_text()).strip()
                    for o in await sel_el.locator("option").all()
                    if (await o.inner_text()).strip() not in ("", "--", "Select")
                ]
                el_id = await sel_el.get_attribute("id") or ""
                sel = f"#{el_id}" if el_id else "select"
                field = FormField(label=label, field_type="select", selector=sel, options=options)
                await self._fill_field(page, field, job)
            except Exception:
                continue

    async def _get_label(self, page, element) -> str:
        aria = await element.get_attribute("aria-label") or ""
        if aria:
            return aria.strip()
        el_id = await element.get_attribute("id") or ""
        if el_id:
            lbl = page.locator(f'label[for="{el_id}"]')
            if await lbl.count():
                return (await lbl.inner_text()).strip()
        placeholder = await element.get_attribute("placeholder") or ""
        if placeholder:
            return placeholder.strip()
        name = await element.get_attribute("name") or ""
        return name.replace("-", " ").replace("_", " ").title()
