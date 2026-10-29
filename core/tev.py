"""tev.py

Document classification with the tev1 decision model (Ollama `systemone`).

tev1 scores a fixed set of options against a short text "state" and returns
a probability per option, rather than generating text, so classification
is a single forward pass with a closed set of answers.

The model reads about 2k tokens of context, so the state is a digest: lines
that name the form first (titles, "Form ...", "Schedule ..."), then the
document's opening text, capped at `tev_max_state_chars`.

*Usage:*
>>> classifier = TevClassifier(settings)
>>> result = await classifier.classify("Form W-2 Wage and Tax Statement 2025 ...")
>>> result.document_type, round(result.confidence, 2)
('w2', 0.98)
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field

import httpx
import ollama

from core.config import Settings
from core.forms import FORMS, UNKNOWN

log = logging.getLogger(__name__)

NONE_OPTION = "none"
_TITLE_LINE = re.compile(
    r"\b(form|schedule)\s+(w-?2|1099|1098|1095|ssa|k-?1|1065|1040)|wage and tax statement|benefit statement"
    r"|interest income|dividends and distributions|nonemployee compensation|mortgage interest"
    r"|tuition statement|student loan interest|government payments|partner'?s share"
    r"|proceeds from broker|payment card and third party|marketplace statement",
    re.IGNORECASE,
)


class ClassifierUnavailable(RuntimeError):
    """Ollama is unreachable or the tev model is not pulled."""


@dataclass
class Classification:
    document_type: str  # a FORMS key, or "unknown"
    label: str | None  # human-readable form title
    confidence: float  # probability of the chosen option
    probabilities: dict[str, float] = field(default_factory=dict)
    model: str = ""
    elapsed_ms: int = 0

    @property
    def is_known(self) -> bool:
        return self.document_type != UNKNOWN


def criteria() -> dict[str, str]:
    """Option -> description, one per supported form plus a 'none' escape hatch."""
    options = {form_id: f"{form.TITLE}. {form.HINT}" for form_id, form in FORMS.items()}
    options[NONE_OPTION] = (
        "Not one of the listed tax forms: e.g. an invoice, receipt, letter, bank statement, "
        "tax return (Form 1040), or a different tax form."
    )
    return options


def build_state(text: str, max_chars: int) -> str:
    """
    Compact classification input: form-identifying lines first, then the
    beginning of the document.

    *Usage:*
    >>> build_state("Some header\\nForm W-2 Wage and Tax Statement\\n...", 3000)
    'Form W-2 Wage and Tax Statement\\n---\\nSome header\\n...'
    """
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    titles = list(dict.fromkeys(line[:200] for line in lines if _TITLE_LINE.search(line)))[:8]
    head = "\n".join(titles)
    body = "\n".join(lines)
    state = (head + "\n---\n" + body) if head else body
    return state[:max_chars]


class TevClassifier:
    def __init__(self, settings: Settings, client: ollama.AsyncClient | None = None):
        self.settings = settings
        self.model = settings.tev_model
        self.client = client or ollama.AsyncClient(host=settings.ollama_url, timeout=settings.ollama_timeout_s)
        self._criteria = criteria()

    async def warmup(self) -> None:
        """Load the model into Ollama memory so the first request is not slow."""
        await self.classify("Form W-2 Wage and Tax Statement")

    async def classify(self, text: str) -> Classification:
        started = time.perf_counter()
        state = build_state(text, self.settings.tev_max_state_chars)
        if not state.strip():
            return Classification(UNKNOWN, None, 0.0, {}, self.model, 0)
        try:
            response = await self.client.systemone(
                model=self.model,
                state=state,
                questions={
                    "document_type": {
                        "type": "choice",
                        "instructions": "Which US tax form is this document? Judge by the form number and title printed on it.",
                        "criteria": self._criteria,
                    },
                },
                keep_alive=self.settings.ollama_keep_alive,
            )
        except (httpx.HTTPError, ConnectionError) as exc:
            raise ClassifierUnavailable(f"Ollama is unreachable at {self.settings.ollama_url}: {exc}") from exc
        except ollama.ResponseError as exc:
            raise ClassifierUnavailable(f"tev model error ({self.model}): {exc.error}") from exc

        answer = response.answers["document_type"]
        probabilities = {k: round(v, 6) for k, v in sorted(answer.probabilities.items(), key=lambda kv: -kv[1])}
        choice = answer.choice
        confidence = float(answer.probabilities.get(choice, 0.0))
        document_type = choice if choice in FORMS and confidence >= self.settings.tev_min_confidence else UNKNOWN
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        log.info("classified", extra={"ctx": {
            "document_type": document_type, "choice": choice, "confidence": round(confidence, 4),
            "state_chars": len(state), "elapsed_ms": elapsed_ms,
        }})
        return Classification(
            document_type=document_type,
            label=FORMS[document_type].TITLE if document_type in FORMS else None,
            confidence=confidence,
            probabilities=probabilities,
            model=self.model,
            elapsed_ms=elapsed_ms,
        )


class HeadingClassifier:
    """
    Classification from the printed form heading alone (`TEV_MODEL=none`):
    no model, no VRAM, and no dependency on the tev1 weights, whose release
    license is not final. Same interface as `TevClassifier`.
    """

    model = "headings"

    async def warmup(self) -> None:
        return None

    async def classify(self, text: str) -> Classification:
        from core.segment import find_headings
        from core.layout import Block

        started = time.perf_counter()
        lines = [Block(1, 0.0, float(i), 1000.0, float(i) + 0.5, line) for i, line in enumerate(text.splitlines()) if line.strip()]
        found = [h.form_id for h in find_headings(lines)]
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        if not found:
            return Classification(UNKNOWN, None, 0.0, {NONE_OPTION: 1.0}, self.model, elapsed_ms)
        form_id = max(set(found), key=found.count)
        share = round(found.count(form_id) / len(found), 6)
        return Classification(form_id, FORMS[form_id].TITLE, share, {form_id: share}, self.model, elapsed_ms)

