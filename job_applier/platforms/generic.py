"""Generic HTML form handler for arbitrary job posting URLs."""
from __future__ import annotations

import logging
import re
from typing import Optional

from job_applier.platforms.base import (
    ApplicationResult,
    BasePlatform,
    FormField,
    JobListing,
)
from job_applier.utils.errors import FormFillError, SubmissionError

logger = logging.getLogger("job_applier.platforms.generic")


class GenericPlatform(BasePlatform):

    async def search_jobs(self) -> list[JobListing]:
        urls = self._config.platforms.generic_urls.urls
        return [
            JobListing(
                url=url,
                title="Unknown",
                company="Unknown",
                platform="generic",
                job_id=re.sub(r"[^\w]", "_", url)[-50:],
            )
            for url in urls
        ]

    async def apply(self, job: JobListing) -> ApplicationResult:
        if self._config.behavior.dry_run:
            logger.info("[DRY RUN] Would apply to %s", job.url)
            return ApplicationResult(job=job, status="dry_run")

        page = await self._session.new_page()
        try:
            await page.goto(job.url, timeout=30000, wait_until="domcontentloaded")
            await self._session.short_delay(1000, 2000)

            ok = await self._detect_and_fill_form(page, job)
            if not ok:
                return ApplicationResult(
                    job=job, status="skipped",
                    error_message="No form fields detected"
                )
            logger.info("Submitted application to %s", job.url)
            return ApplicationResult(job=job, status="submitted")

        except Exception as e:
            logger.exception("Error applying to %s: %s", job.url, e)
            return ApplicationResult(
                job=job, status="error", error_message=str(e)
            )
        finally:
            await page.close()

    async def _detect_and_fill_form(self, page, job: JobListing) -> bool:
        """Detect, fill and submit the application form on the current page.

        Returns True if the form was successfully submitted, False otherwise.
        Called both by apply() and by other platforms delegating to generic handler.
        """
        fields = await self._discover_fields(page)
        if not fields:
            logger.warning("No form fields found at %s", job.url)
            return False

        for field in fields:
            await self._fill_field(page, field, job)
            await self._session.short_delay(300, 800)

        await self._submit_form(page)
        await self._session.short_delay(2000, 3000)
        return True

    async def _discover_fields(self, page) -> list[FormField]:
        """Find the best application form and enumerate its fields."""
        # Find all forms and score them
        forms = await page.locator("form").all()
        best_form = None
        best_score = -1

        for form in forms:
            try:
                html = await form.inner_html()
                score = self._score_form(html)
                if score > best_score:
                    best_score = score
                    best_form = form
            except Exception:
                continue

        if best_form is None or best_score < 1:
            # Fall back to whole page if no form found
            best_form = page

        return await self._enumerate_fields(page, best_form)

    def _score_form(self, html: str) -> int:
        keywords = [
            "resume", "cv", "apply", "name", "email", "phone",
            "experience", "cover letter", "linkedin", "upload",
        ]
        html_lower = html.lower()
        return sum(1 for kw in keywords if kw in html_lower)

    async def _enumerate_fields(self, page, container) -> list[FormField]:
        fields: list[FormField] = []

        # Text-like inputs
        inputs = await container.locator(
            'input:not([type="hidden"]):not([type="submit"]):not([type="button"])'
            ':not([type="reset"]):not([type="image"])'
        ).all()

        for inp in inputs:
            try:
                field = await self._build_input_field(page, inp)
                if field:
                    fields.append(field)
            except Exception:
                continue

        # Textareas
        textareas = await container.locator("textarea").all()
        for ta in textareas:
            try:
                label = await self._find_label(page, ta) or "Message"
                sel = await self._unique_selector(ta)
                if sel:
                    fields.append(FormField(
                        label=label,
                        field_type="textarea",
                        selector=sel,
                    ))
            except Exception:
                continue

        # Selects
        selects = await container.locator("select").all()
        for sel_el in selects:
            try:
                label = await self._find_label(page, sel_el) or "Selection"
                options = await self._get_select_options(sel_el)
                sel = await self._unique_selector(sel_el)
                if sel:
                    fields.append(FormField(
                        label=label,
                        field_type="select",
                        selector=sel,
                        options=options,
                    ))
            except Exception:
                continue

        return fields

    async def _build_input_field(self, page, inp) -> Optional[FormField]:
        itype = (await inp.get_attribute("type") or "text").lower()

        if itype in ("hidden", "submit", "button", "reset", "image"):
            return None

        label = await self._find_label(page, inp) or await inp.get_attribute("placeholder") or "Field"
        sel = await self._unique_selector(inp)
        if not sel:
            return None

        if itype == "file":
            return FormField(label=label, field_type="file", selector=sel)
        if itype in ("checkbox",):
            return FormField(label=label, field_type="checkbox", selector=sel)
        if itype in ("radio",):
            # Group radios by name
            name = await inp.get_attribute("name") or ""
            return FormField(
                label=label, field_type="radio", selector=name, options=[]
            )
        if itype == "email":
            return FormField(label=label, field_type="email", selector=sel)
        if itype == "tel":
            return FormField(label=label, field_type="tel", selector=sel)
        if itype == "number":
            return FormField(label=label, field_type="number", selector=sel)

        return FormField(label=label, field_type="text", selector=sel)

    async def _find_label(self, page, element) -> str:
        """Find the label text for a form element using multiple strategies."""
        # 1. aria-label attribute
        aria = await element.get_attribute("aria-label") or ""
        if aria.strip():
            return aria.strip()

        # 2. id-based <label for="...">
        el_id = await element.get_attribute("id") or ""
        if el_id:
            try:
                label_el = page.locator(f'label[for="{el_id}"]')
                if await label_el.count() > 0:
                    text = await label_el.first.inner_text()
                    if text.strip():
                        return text.strip()
            except Exception:
                pass

        # 3. Placeholder
        placeholder = await element.get_attribute("placeholder") or ""
        if placeholder.strip():
            return placeholder.strip()

        # 4. name attribute as fallback
        name = await element.get_attribute("name") or ""
        return name.replace("_", " ").replace("-", " ").title()

    async def _unique_selector(self, element) -> str:
        """Generate a CSS selector that uniquely identifies this element."""
        el_id = await element.get_attribute("id") or ""
        if el_id:
            return f"#{el_id}"

        name = await element.get_attribute("name") or ""
        if name:
            itype = await element.get_attribute("type") or ""
            if itype:
                return f'[name="{name}"][type="{itype}"]'
            return f'[name="{name}"]'

        return ""

    async def _get_select_options(self, select_el) -> list[str]:
        options = await select_el.locator("option").all()
        texts = []
        for opt in options:
            text = (await opt.inner_text()).strip()
            if text and text not in ("--", "Select", "Choose", ""):
                texts.append(text)
        return texts

    async def _submit_form(self, page) -> None:
        selectors = [
            'button[type="submit"]',
            'input[type="submit"]',
            'button:has-text("Submit")',
            'button:has-text("Apply")',
            'button:has-text("Send")',
        ]
        for sel in selectors:
            try:
                btn = page.locator(sel).first
                if await btn.count() > 0 and await btn.is_visible():
                    await btn.click()
                    return
            except Exception:
                continue
        raise SubmissionError("Could not find a submit button on the page")
