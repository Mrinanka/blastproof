"""Chat models from `provider:model` strings (ARCH-19), their prices, and lazy building.

Shared by the impact summary (step 12) and the judge (step 13). Nothing here calls a model: the
callers do, with their own timeout. Options from config are passed to the model's constructor and
never logged, because they could hold a key.
"""

import asyncio
import re
import unicodedata
from collections.abc import Callable, Mapping
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Any, Final

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

    from app.config import Settings

# Keys `init_chat_model` already sets itself, or reads as its own arguments: an option with one of
# these names would collide or silently change how the model is built.
RESERVED_OPTION_KEYS: Final = frozenset(
    {
        "model",
        "model_provider",
        "timeout",
        "max_tokens",
        "max_retries",
        "configurable_fields",
        "config_prefix",
    }
)
# `provider:model` (ARCH-19). init_chat_model takes the part before the first colon as the provider
# only when it is one of its lowercase provider names; anything else, or a bare name, makes it guess
# the provider from the model name (a bare `gemini-...` becomes Vertex AI, not google_genai).
MODEL_STRING: Final = re.compile(r"[a-z][a-z0-9_]*:\S+")
# For Google, max_retries=1 means a single attempt: no retry inside our timeout.
MAX_RETRIES: Final = 1
COST_QUANTUM: Final = Decimal("0.000001")  # actions.llm_cost_usd is Numeric(12, 6)
_MILLION: Final = Decimal(1_000_000)


class LlmConfigError(ValueError):
    """The model settings cannot work (for example no price for the model). Stops startup."""


class LlmPrice(BaseModel):
    """US dollars per million tokens. Code computes the cost; it never comes from a model."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    input_per_million: Decimal = Field(ge=0)
    output_per_million: Decimal = Field(ge=0)


def price_for(model: str, prices: Mapping[str, LlmPrice]) -> LlmPrice:
    """The model's price, or `LlmConfigError`: a cost must never be silently unknown."""
    price = prices.get(model)
    if price is None:
        raise LlmConfigError(
            f"LLM_PRICES has no entry for {model!r}; add its price per million input and output"
            " tokens so its cost can be recorded."
        )
    return price


def cost_usd(price: LlmPrice, input_tokens: int, output_tokens: int) -> Decimal:
    """tokens x price, in Decimal, rounded to the column's 6 decimal places."""
    total = (
        Decimal(input_tokens) * price.input_per_million
        + Decimal(output_tokens) * price.output_per_million
    ) / _MILLION
    return total.quantize(COST_QUANTUM, rounding=ROUND_HALF_UP)


def build_chat_model(
    model: str, options: Mapping[str, Any], settings: "Settings"
) -> "BaseChatModel":
    """`init_chat_model` with our limits. The provider comes only from the `provider:` prefix.

    Never passes `temperature`: Gemini 3.6 and later deprecate custom sampling.
    """
    from langchain.chat_models import init_chat_model

    return init_chat_model(
        model,
        timeout=settings.llm_timeout_seconds,
        max_tokens=settings.llm_max_tokens,
        max_retries=MAX_RETRIES,
        **options,
    )


ModelFactory = Callable[[str, Mapping[str, Any], "Settings"], Any]


def usage_tokens(raw: object) -> tuple[int, int] | None:
    """(input, output) tokens from a raw model message, or None when the usage is unknown."""
    usage = getattr(raw, "usage_metadata", None)
    if not isinstance(usage, Mapping):
        return None
    tokens_in, tokens_out = usage.get("input_tokens"), usage.get("output_tokens")
    if not isinstance(tokens_in, int) or not isinstance(tokens_out, int):
        return None
    return tokens_in, tokens_out


# Whitespace a sentence may hold. Any other control character, \x00 above all, cannot be stored:
# Postgres text and JSONB reject \u0000, and a lone surrogate cannot be encoded as UTF-8.
_ALLOWED_CONTROLS: Final = frozenset("\n\r\t")


def text_ok(text: str) -> bool:
    """Model text that can be stored and has something visible in it.

    False for any control character but line breaks and tab, any lone surrogate, and text made only
    of spaces, separators (NBSP) and format characters (zero-width space). A model once answered
    with nothing but those and a NUL, which passed `.strip()` and then failed the database write.
    """
    visible = False
    for ch in text:
        category = unicodedata.category(ch)
        if category == "Cs" or (category == "Cc" and ch not in _ALLOWED_CONTROLS):
            return False
        if category != "Cf" and category[0] != "Z" and not ch.isspace():
            visible = True
    return visible


class LazyModel[T]:
    """Builds its value on first use and keeps it. A failed build is retried on the next call.

    Concurrent first calls wait for one build instead of each building a model.
    """

    def __init__(self, build: Callable[[], T]) -> None:
        self._build = build
        self._value: T | None = None
        self._lock = asyncio.Lock()

    async def get(self) -> T:
        if self._value is not None:
            return self._value
        async with self._lock:
            if self._value is None:
                self._value = self._build()
            return self._value


__all__ = [
    "MODEL_STRING",
    "RESERVED_OPTION_KEYS",
    "LazyModel",
    "LlmConfigError",
    "LlmPrice",
    "ModelFactory",
    "build_chat_model",
    "cost_usd",
    "price_for",
    "usage_tokens",
]
