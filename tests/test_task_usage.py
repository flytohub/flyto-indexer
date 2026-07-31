"""Provider-neutral usage normalization tests."""

import pytest

from src.task_usage import (
    estimate_token_usage,
    estimate_usage_from_char_counts,
    normalize_provider_usage,
)


@pytest.mark.parametrize(
    ("provider", "metadata", "expected"),
    [
        (
            "openai",
            {
                "input_tokens": 120,
                "output_tokens": 30,
                "input_tokens_details": {"cached_tokens": 20},
                "output_tokens_details": {"reasoning_tokens": 8},
            },
            (120, 30, 20, 8),
        ),
        (
            "anthropic",
            {
                "input_tokens": 100,
                "output_tokens": 40,
                "cache_read_input_tokens": 15,
                "cache_creation_input_tokens": 5,
            },
            (100, 40, 20, 0),
        ),
        (
            "gemini",
            {
                "prompt_token_count": 90,
                "candidates_token_count": 25,
                "cached_content_token_count": 10,
                "thoughts_token_count": 3,
            },
            (90, 25, 10, 3),
        ),
        (
            "generic",
            {"prompt_tokens": 70, "completion_tokens": 20},
            (70, 20, 0, 0),
        ),
    ],
)
def test_normalize_provider_usage(provider, metadata, expected):
    usage = normalize_provider_usage(provider, metadata)

    assert (
        usage.input_tokens,
        usage.output_tokens,
        usage.cached_input_tokens,
        usage.reasoning_tokens,
    ) == expected
    assert usage.source == "reported"


@pytest.mark.parametrize(
    "metadata",
    [
        {},
        {"input_tokens": -1, "output_tokens": 2},
        {"input_tokens": True, "output_tokens": 2},
    ],
)
def test_normalize_rejects_missing_or_invalid_counts(metadata):
    with pytest.raises(ValueError):
        normalize_provider_usage("openai", metadata)


def test_heuristic_estimation_is_deterministic_and_text_free():
    first = estimate_token_usage(
        "hello 世界",
        "done",
        prefer_tiktoken=False,
    )
    second = estimate_token_usage(
        "hello 世界",
        "done",
        prefer_tiktoken=False,
    )

    assert first == second
    assert first.source == "estimated"
    assert first.estimator == "heuristic:unicode-v1"
    assert "hello" not in str(first.to_dict())


def test_character_count_estimator_never_accepts_raw_text():
    usage = estimate_usage_from_char_counts(401, 39)

    assert usage.input_tokens == 101
    assert usage.output_tokens == 10
    assert usage.estimator == "heuristic:chars-v1"
