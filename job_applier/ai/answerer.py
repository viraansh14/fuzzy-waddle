"""Claude-powered question answerer for job application forms."""
from __future__ import annotations

import difflib
import logging
import re
from typing import Literal

import anthropic

from job_applier.ai.prompts import OPTIONS_BLOCK, QUESTION_PROMPT, SYSTEM_PROMPT
from job_applier.cv.models import CVProfile

logger = logging.getLogger("job_applier.ai")

FieldType = Literal["text", "textarea", "select", "radio", "checkbox", "number", "yesno"]


class AIAnswerer:
    def __init__(
        self,
        client: anthropic.Anthropic,
        model: str,
        cv_profile: CVProfile,
        max_tokens: int = 512,
    ):
        self._client = client
        self._model = model
        self._cv = cv_profile
        self._max_tokens = max_tokens
        self._cache: dict[tuple, str] = {}
        self._system_prompt = SYSTEM_PROMPT.format(
            cv_context=f"CANDIDATE CV:\n{cv_profile.to_context_string()}"
        )

    def answer(
        self,
        question: str,
        field_type: FieldType = "text",
        options: list[str] | None = None,
        job_title: str = "",
        company: str = "",
    ) -> str:
        cache_key = (question, field_type, tuple(options or []))
        if cache_key in self._cache:
            logger.debug("Cache hit for question: %s", question[:60])
            return self._cache[cache_key]

        options_block = ""
        if options:
            options_list = "\n".join(f"  - {o}" for o in options)
            options_block = OPTIONS_BLOCK.format(options_list=options_list)

        user_msg = QUESTION_PROMPT.format(
            job_title=job_title or "the role",
            company=company or "the company",
            question=question,
            options_block=options_block,
        )

        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=self._system_prompt,
                messages=[{"role": "user", "content": user_msg}],
            )
            raw_answer = response.content[0].text.strip()
        except anthropic.APIError as e:
            logger.warning("Claude API error for question '%s': %s", question[:60], e)
            raw_answer = ""

        answer = self._normalize(raw_answer, field_type, options)
        self._cache[cache_key] = answer
        logger.debug("Q: %s → A: %s", question[:60], answer[:80])
        return answer

    def _normalize(
        self, raw: str, field_type: FieldType, options: list[str] | None
    ) -> str:
        if field_type == "yesno":
            lower = raw.lower()
            if "yes" in lower:
                return "Yes"
            if "no" in lower:
                return "No"
            return "Yes"

        if field_type == "number":
            digits = re.sub(r"[^\d]", "", raw)
            return digits if digits else "0"

        if field_type in ("select", "radio") and options:
            return self._best_match(raw, options)

        # text / textarea / checkbox — return as-is
        return raw

    @staticmethod
    def _best_match(answer: str, options: list[str]) -> str:
        """Find the closest matching option using difflib."""
        answer_lower = answer.lower()

        # Exact match first
        for opt in options:
            if opt.lower() == answer_lower:
                return opt

        # Substring match
        for opt in options:
            if answer_lower in opt.lower() or opt.lower() in answer_lower:
                return opt

        # Fuzzy match
        matches = difflib.get_close_matches(
            answer_lower, [o.lower() for o in options], n=1, cutoff=0.4
        )
        if matches:
            idx = [o.lower() for o in options].index(matches[0])
            return options[idx]

        # Default to first option
        return options[0]
