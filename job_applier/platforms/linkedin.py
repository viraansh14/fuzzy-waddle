"""LinkedIn Easy Apply automation."""
from __future__ import annotations

import logging
import re
from urllib.parse import urlencode

from job_applier.platforms.base import (
    ApplicationResult,
    BasePlatform,
    FormField,
    JobListing,
)
from job_applier.utils.errors import BotDetectionError, SubmissionError

logger = logging.getLogger("job_applier.platforms.linkedin")

_SEARCH_BASE = "https://www.linkedin.com/jobs/search/?"


class LinkedInPlatform(BasePlatform):

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
        return unique[: self._config.platforms.linkedin.max_per_run]

    async def _search_page(
        self, page, title: str, location: str
    ) -> list[JobListing]:
        params = {
            "keywords": title,
            "location": location,
            "f_LF": "f_AL",  # Easy Apply filter
            "sortBy": "DD",  # Date descending
        }
        url = _SEARCH_BASE + urlencode(params)
        await page.goto(url, timeout=30000, wait_until="domcontentloaded")
        await self._session.short_delay(2000, 3000)

        if "captcha" in page.url.lower() or "checkpoint" in page.url.lower():
            raise BotDetectionError("LinkedIn bot detection triggered")

        listings: list[JobListing] = []
        cards = await page.locator(
            "ul.scaffold-layout__list-container > li[data-occludable-job-id],"
            "ul.jobs-search__results-list > li"
        ).all()

        for card in cards[:20]:
            try:
                job_id = await card.get_attribute("data-occludable-job-id") or ""
                if not job_id:
                    job_id = await card.get_attribute("data-job-id") or ""

                title_el = card.locator("a.job-card-list__title, .job-card-container__link")
                company_el = card.locator(".job-card-container__primary-description, .job-card-container__company-name")

                job_title = (await title_el.inner_text()).strip() if await title_el.count() else title
                company = (await company_el.inner_text()).strip() if await company_el.count() else ""

                href = await title_el.get_attribute("href") if await title_el.count() else ""
                if href and not href.startswith("http"):
                    href = "https://www.linkedin.com" + href

                # Extract clean job ID from href if not found on element
                if not job_id and href:
                    m = re.search(r"/jobs/view/(\d+)", href)
                    if m:
                        job_id = m.group(1)

                if job_id and href:
                    listings.append(JobListing(
                        url=href.split("?")[0],
                        title=job_title,
                        company=company,
                        platform="linkedin",
                        job_id=f"linkedin:{job_id}",
                        easy_apply=True,
                    ))
            except Exception:
                continue

        return listings

    async def apply(self, job: JobListing) -> ApplicationResult:
        if self._config.behavior.dry_run:
            logger.info("[DRY RUN] Would apply to LinkedIn: %s @ %s", job.title, job.company)
            return ApplicationResult(job=job, status="dry_run")

        page = await self._session.new_page()
        try:
            await page.goto(job.url, timeout=30000, wait_until="domcontentloaded")
            await self._session.short_delay(1500, 2500)

            if "captcha" in page.url.lower():
                raise BotDetectionError("LinkedIn CAPTCHA detected")

            # Click Easy Apply button
            easy_apply_btn = page.locator(
                'button.jobs-apply-button[aria-label*="Easy Apply"],'
                'button[aria-label*="Easy Apply"]'
            ).first
            if not await easy_apply_btn.count():
                return ApplicationResult(
                    job=job, status="skipped",
                    error_message="No Easy Apply button found"
                )

            await easy_apply_btn.click()
            await self._session.short_delay(1500, 2000)

            # Handle the multi-step modal
            await self._handle_modal(page, job)
            return ApplicationResult(job=job, status="submitted")

        except BotDetectionError as e:
            return ApplicationResult(job=job, status="error", error_message=str(e))
        except SubmissionError as e:
            return ApplicationResult(job=job, status="error", error_message=str(e))
        except Exception as e:
            logger.exception("LinkedIn apply error for %s: %s", job.url, e)
            return ApplicationResult(job=job, status="error", error_message=str(e))
        finally:
            await page.close()

    async def _handle_modal(self, page, job: JobListing) -> None:
        modal = page.locator(
            ".jobs-easy-apply-modal, [data-test-modal-id='easy-apply-modal']"
        )
        max_steps = 10

        for step in range(max_steps):
            await self._session.short_delay(1000, 1500)

            # Check for submit button — final step
            submit_btn = page.locator(
                'button[aria-label="Submit application"],'
                'button:has-text("Submit application")'
            ).first
            if await submit_btn.count() and await submit_btn.is_visible():
                await submit_btn.click()
                logger.info("Submitted LinkedIn application: %s @ %s", job.title, job.company)
                return

            # Fill any fields on this step
            await self._fill_modal_page(page, job)
            await self._session.short_delay(800, 1200)

            # Click Next / Review / Continue
            next_btn = page.locator(
                'button[aria-label="Continue to next step"],'
                'button[aria-label="Review your application"],'
                'button:has-text("Next"),'
                'button:has-text("Review"),'
                'button:has-text("Continue")'
            ).first
            if await next_btn.count() and await next_btn.is_visible():
                await next_btn.click()
            else:
                raise SubmissionError(
                    f"No navigation button found at step {step + 1}"
                )

        raise SubmissionError("Exceeded max steps in Easy Apply modal")

    async def _fill_modal_page(self, page, job: JobListing) -> None:
        modal_content = page.locator(
            ".jobs-easy-apply-content, .jobs-easy-apply-modal__content"
        )

        # File upload
        file_inputs = await modal_content.locator('input[type="file"]').all()
        for fi in file_inputs:
            try:
                await fi.set_input_files(self._cv_path())
                logger.debug("Uploaded CV file")
            except Exception:
                pass

        # Text/number inputs
        inputs = await modal_content.locator(
            'input[type="text"], input[type="number"], input[type="tel"]'
        ).all()
        for inp in inputs:
            try:
                label = await self._get_linkedin_label(page, inp)
                el_id = await inp.get_attribute("id") or ""
                sel = f"#{el_id}" if el_id else 'input[type="text"]'
                current = await inp.input_value()
                if current.strip():
                    continue  # Already filled (e.g. pre-filled by LinkedIn)
                field = FormField(label=label, field_type="text", selector=sel)
                await self._fill_field(page, field, job)
            except Exception:
                continue

        # Selects
        selects = await modal_content.locator("select").all()
        for sel_el in selects:
            try:
                label = await self._get_linkedin_label(page, sel_el)
                options = [
                    await o.get_attribute("value") or await o.inner_text()
                    for o in await sel_el.locator("option").all()
                ]
                options = [o.strip() for o in options if o.strip()]
                el_id = await sel_el.get_attribute("id") or ""
                sel = f"#{el_id}" if el_id else "select"
                field = FormField(
                    label=label, field_type="select", selector=sel, options=options
                )
                await self._fill_field(page, field, job)
            except Exception:
                continue

        # Radio fieldsets
        fieldsets = await modal_content.locator("fieldset").all()
        for fs in fieldsets:
            try:
                legend = fs.locator("legend, .jobs-easy-apply-form-element__label")
                label = (await legend.inner_text()).strip() if await legend.count() else ""
                radios = await fs.locator('input[type="radio"]').all()
                option_labels: list[str] = []
                for radio in radios:
                    rid = await radio.get_attribute("id") or ""
                    lbl = page.locator(f'label[for="{rid}"]')
                    opt_text = (await lbl.inner_text()).strip() if await lbl.count() else rid
                    option_labels.append(opt_text)

                if not option_labels:
                    continue

                answer = self._answerer.answer(
                    question=label,
                    field_type="radio",
                    options=option_labels,
                    job_title=job.title,
                    company=job.company,
                )
                best = answer.lower()
                for i, radio in enumerate(radios):
                    if option_labels[i].lower() == best:
                        await radio.click()
                        break
            except Exception:
                continue

    async def _get_linkedin_label(self, page, element) -> str:
        aria = await element.get_attribute("aria-label") or ""
        if aria:
            return aria.strip()
        el_id = await element.get_attribute("id") or ""
        if el_id:
            lbl = page.locator(f'label[for="{el_id}"]')
            if await lbl.count():
                return (await lbl.inner_text()).strip()
        name = await element.get_attribute("name") or ""
        return name.replace("-", " ").replace("_", " ").title()
