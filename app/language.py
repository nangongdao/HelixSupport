"""Multi-language customer service (backlog: 多语言客服).

Language detection is script-based and deterministic (no dependencies): the
customer message's dominant Unicode script maps to an ISO 639-1-ish language
code. When a ``ModelProvider`` is configured its detection is preferred and
any failure (provider error, malformed JSON, unknown code) falls back to the
rules, so the turn path never waits on an external model.

Reply translation follows the same model-first contract: the assistant reply
is translated to the customer's language when it differs from the service
language; on any failure the original reply is returned unchanged and marked
``translated=False`` so the turn always completes.
"""

from __future__ import annotations

import json
import logging
import unicodedata
from typing import Any

from app.cost_attribution import InferenceContext, record_model_response
from app.model_provider import ModelProvider

logger = logging.getLogger("helix")

# ISO 639-1 language codes understood by detection/translation, with display
# names for the operator console (also mirrored in the frontend i18n packs).
KNOWN_LANGUAGES = frozenset(
    {"zh", "en", "ja", "ko", "ru", "ar", "hi", "he", "th", "el", "es", "fr", "de", "pt"}
)

LANGUAGE_NAMES: dict[str, str] = {
    "zh": "中文",
    "en": "English",
    "ja": "日本語",
    "ko": "한국어",
    "ru": "Русский",
    "ar": "العربية",
    "hi": "हिन्दी",
    "he": "עברית",
    "th": "ไทย",
    "el": "Ελληνικά",
    "es": "Español",
    "fr": "Français",
    "de": "Deutsch",
    "pt": "Português",
}

DETECT_SYSTEM_PROMPT = (
    "You are a language identification service. Return ONLY a JSON object "
    'with one key "language" whose value is an ISO 639-1 code (zh, en, ja, '
    "ko, ru, ar, hi, he, th, el, es, fr, de, pt) for the language of the "
    "customer message."
)

TRANSLATE_SYSTEM_PROMPT = (
    "You are a customer support translator. Return ONLY a JSON object with "
    'one key "translation" whose value is the customer-facing reply '
    "translated into the target language. Keep names, order numbers, and "
    "figures exactly as they appear."
)

_TRANSLATE_USER_PROMPT = "Target language: {language}\n\nReply to translate:\n{text}"

# Unicode script blocks used by the deterministic detector, ordered so more
# specific scripts (Japanese kana, Korean hangul) win over shared ones
# (Han ideographs). Each entry: (language, tuple of (start, end) ranges).
_SCRIPT_RANGES: list[tuple[str, tuple[tuple[int, int], ...]]] = [
    ("ja", ((0x3040, 0x309F), (0x30A0, 0x30FF))),  # hiragana, katakana
    ("ko", ((0xAC00, 0xD7AF), (0x1100, 0x11FF), (0x3130, 0x318F))),  # hangul
    ("zh", ((0x4E00, 0x9FFF), (0x3400, 0x4DBF), (0xF900, 0xFAFF))),  # CJK
    ("ru", ((0x0400, 0x04FF),)),  # cyrillic
    ("ar", ((0x0600, 0x06FF), (0x0750, 0x077F))),  # arabic
    ("hi", ((0x0900, 0x097F),)),  # devanagari
    ("he", ((0x0590, 0x05FF),)),  # hebrew
    ("th", ((0x0E00, 0x0E7F),)),  # thai
    ("el", ((0x0370, 0x03FF),)),  # greek
    ("pt", ((0x00C0, 0x00FF),)),  # latin-1 supplement -> latin family
]


def detect_language(text: str) -> str | None:
    """Detect the dominant language of ``text`` from its Unicode scripts.

    Returns an ISO 639-1 code, or None when the text carries no detectable
    script signal (empty/whitespace-only or punctuation-only input). Latin
    text maps to ``en`` by default.
    """
    counts: dict[str, int] = {}
    latin = 0
    other = 0
    for char in text:
        code = ord(char)
        matched = False
        for language, ranges in _SCRIPT_RANGES:
            if any(start <= code <= end for start, end in ranges):
                counts[language] = counts.get(language, 0) + 1
                matched = True
                break
        if matched:
            continue
        if code > 0x7F:
            category = unicodedata.category(char)
            if category.startswith("L"):
                other += 1
            continue
        if char.isalpha():
            latin += 1
    if latin:
        counts["en"] = counts.get("en", 0) + latin
    if not counts and other:
        # Non-Latin alphabetic scripts without an explicit range (e.g. some
        # supplementary-plane scripts) have no confident mapping.
        return None
    if not counts:
        return None
    # Japanese vs Chinese: kana characters only occur in Japanese, so on an
    # equal count the Japanese block wins; otherwise the larger script block
    # decides, with the latin default (``en``) losing ties to any specific
    # script (insertion order prefers script ranges over the en default).
    dominant = max(counts.items(), key=lambda pair: (pair[1], pair[0] == "ja"))[0]
    return dominant if dominant in KNOWN_LANGUAGES else None


def language_display_name(language: str | None) -> str:
    if language is None:
        return ""
    return LANGUAGE_NAMES.get(language, language)


class LanguageService:
    """Detects customer languages and translates replies (model-first)."""

    def __init__(
        self,
        model_provider: ModelProvider | None = None,
        service_language: str = "zh",
        cost_attribution: Any = None,
    ) -> None:
        self.model_provider = model_provider
        self.service_language = service_language
        self.cost_attribution = cost_attribution

    def _record_cost(self, tenant_id: str | None, response: Any, agent: str) -> None:
        if tenant_id is None:
            return
        record_model_response(
            self.cost_attribution,
            tenant_id,
            response,
            InferenceContext(agent=agent),
        )

    # ------------------------------------------------------------- detection

    def detect(self, text: str, tenant_id: str | None = None) -> tuple[str | None, str]:
        """Return (language, source) for a customer message.

        ``source`` is ``"model"`` when the provider answered with a known
        code, otherwise ``"rule"``. Never raises: provider failures and
        unusable output fall back to the deterministic detector.
        """
        if self.model_provider is not None:
            try:
                response = self.model_provider.complete(
                    DETECT_SYSTEM_PROMPT, f"Customer message:\n{text[:2000]}"
                )
                self._record_cost(tenant_id, response, "language_detect")
                payload = json.loads(response.content)
                language = payload["language"]
                if isinstance(language, str) and language in KNOWN_LANGUAGES:
                    return language, "model"
            except Exception:
                logger.debug(
                    "language.detect_failed", extra={"provider": type(self.model_provider).__name__}
                )
        return detect_language(text), "rule"

    # ------------------------------------------------------------ translation

    def translate(
        self,
        text: str,
        target_language: str,
        tenant_id: str | None = None,
    ) -> tuple[str, bool, str]:
        """Translate ``text`` to ``target_language``.

        Returns ``(translated_text, translated, source)``. When the target
        matches the service language, or the provider fails/misbehaves, the
        original text is returned with ``translated=False`` — the turn path
        never blocks on translation.
        """
        if not target_language or target_language == self.service_language:
            return text, False, "none"
        if self.model_provider is None:
            return text, False, "rule"
        try:
            response = self.model_provider.complete(
                TRANSLATE_SYSTEM_PROMPT,
                _TRANSLATE_USER_PROMPT.format(language=target_language, text=text[:6000]),
            )
            self._record_cost(tenant_id, response, "language_translate")
            payload = json.loads(response.content)
            translation = payload["translation"]
            if not isinstance(translation, str) or not translation.strip():
                raise ValueError("Translation must be a non-empty string")
            if translation.strip() == text.strip():
                return text, False, "rule"
            return translation.strip(), True, "model"
        except Exception:
            logger.debug(
                "language.translate_failed",
                extra={"target_language": target_language},
            )
            return text, False, "rule"
