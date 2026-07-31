"""Provider-neutral token accounting and honest paired comparisons."""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass
from importlib import import_module
from typing import Any

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")
_WORD_RE = re.compile(r"[A-Za-z0-9_]+|[^\sA-Za-z0-9_]", re.UNICODE)


def _non_negative_int(value: Any) -> int:
    """Coerce a provider count while rejecting booleans and negatives."""
    if value is None:
        return 0
    if isinstance(value, bool):
        raise ValueError("token counts must be integers, not booleans")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("token counts must be non-negative integers") from exc
    if number < 0:
        raise ValueError("token counts must be non-negative integers")
    return number


def _nested_int(data: dict[str, Any], *path: str) -> int:
    """Read an optional nested provider count."""
    value: Any = data
    for key in path:
        if not isinstance(value, dict):
            return 0
        value = value.get(key)
    return _non_negative_int(value)


@dataclass(frozen=True)
class TokenUsage:
    """Normalized counts; cached and reasoning tokens remain informational subsets."""

    input_tokens: int
    output_tokens: int
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0
    source: str = "reported"
    estimator: str = ""

    @property
    def total_tokens(self) -> int:
        """Return billable input plus output tokens."""
        return self.input_tokens + self.output_tokens

    def to_dict(self) -> dict[str, Any]:
        """Serialize normalized counts without any original text."""
        return {**asdict(self), "total_tokens": self.total_tokens}


def normalize_provider_usage(provider: str, metadata: dict[str, Any]) -> TokenUsage:
    """Normalize common OpenAI, Anthropic, Gemini, and generic usage envelopes."""
    if not isinstance(metadata, dict):
        raise ValueError("usage metadata must be a JSON object")
    name = (provider or "generic").strip().lower()
    if name in {"openai", "azure-openai", "azure_openai"}:
        input_tokens = _non_negative_int(
            metadata.get("input_tokens", metadata.get("prompt_tokens"))
        )
        output_tokens = _non_negative_int(
            metadata.get("output_tokens", metadata.get("completion_tokens"))
        )
        cached = _non_negative_int(metadata.get("cached_input_tokens")) or _nested_int(
            metadata, "input_tokens_details", "cached_tokens"
        )
        reasoning = _non_negative_int(metadata.get("reasoning_tokens")) or _nested_int(
            metadata, "output_tokens_details", "reasoning_tokens"
        )
    elif name in {"anthropic", "claude"}:
        input_tokens = _non_negative_int(metadata.get("input_tokens"))
        output_tokens = _non_negative_int(metadata.get("output_tokens"))
        cached = _non_negative_int(metadata.get("cache_read_input_tokens"))
        cached += _non_negative_int(metadata.get("cache_creation_input_tokens"))
        reasoning = _non_negative_int(metadata.get("reasoning_tokens"))
    elif name in {"google", "gemini", "google-gemini"}:
        input_tokens = _non_negative_int(
            metadata.get("prompt_token_count", metadata.get("input_tokens"))
        )
        output_tokens = _non_negative_int(
            metadata.get("candidates_token_count", metadata.get("output_tokens"))
        )
        cached = _non_negative_int(
            metadata.get("cached_content_token_count", metadata.get("cached_input_tokens"))
        )
        reasoning = _non_negative_int(
            metadata.get("thoughts_token_count", metadata.get("reasoning_tokens"))
        )
    else:
        input_tokens = _non_negative_int(
            metadata.get("input_tokens", metadata.get("prompt_tokens"))
        )
        output_tokens = _non_negative_int(
            metadata.get("output_tokens", metadata.get("completion_tokens"))
        )
        cached = _non_negative_int(
            metadata.get("cached_input_tokens", metadata.get("cached_tokens"))
        )
        reasoning = _non_negative_int(metadata.get("reasoning_tokens"))

    if input_tokens == 0 and output_tokens == 0:
        raise ValueError("usage metadata has no input or output token count")
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached,
        reasoning_tokens=reasoning,
        source="reported",
    )


def _heuristic_token_count(text: str) -> int:
    """Estimate mixed Unicode tokens deterministically in memory."""
    if not text:
        return 0
    count = 0
    for piece in _WORD_RE.findall(text):
        if _CJK_RE.fullmatch(piece):
            count += 1
        elif piece.isascii() and (piece.isalnum() or "_" in piece):
            count += max(1, math.ceil(len(piece) / 4))
        else:
            count += max(1, math.ceil(len(piece.encode("utf-8")) / 4))
    return count


def estimate_token_usage(
    input_text: str,
    output_text: str = "",
    *,
    model: str = "",
    prefer_tiktoken: bool = True,
) -> TokenUsage:
    """Count in memory without retaining text; tiktoken remains an optional extra."""
    estimator = "heuristic:unicode-v1"
    input_tokens: int | None = None
    output_tokens: int | None = None
    if prefer_tiktoken:
        try:
            tiktoken = import_module("tiktoken")

            try:
                encoding = tiktoken.encoding_for_model(model) if model else None
            except KeyError:
                encoding = None
            encoding = encoding or tiktoken.get_encoding("o200k_base")
            input_tokens = len(encoding.encode(input_text))
            output_tokens = len(encoding.encode(output_text))
            estimator = f"tiktoken:{encoding.name}"
        except (ImportError, ModuleNotFoundError):
            pass
    if input_tokens is None or output_tokens is None:
        input_tokens = _heuristic_token_count(input_text)
        output_tokens = _heuristic_token_count(output_text)
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        source="estimated",
        estimator=estimator,
    )


def estimate_usage_from_char_counts(input_chars: int, output_chars: int) -> TokenUsage:
    """Conservative CLI fallback when raw text must never cross the interface."""
    input_count = _non_negative_int(input_chars)
    output_count = _non_negative_int(output_chars)
    return TokenUsage(
        input_tokens=math.ceil(input_count / 4),
        output_tokens=math.ceil(output_count / 4),
        source="estimated",
        estimator="heuristic:chars-v1",
    )
