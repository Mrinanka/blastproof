"""Scripted stand-ins for the summary and judge models, for the local demo seed.

`ScriptedModelFactory` is a `ModelFactory` (see `app.services.llm`): `ImpactEngine` and `LLMJudge`
call it exactly as they call `build_chat_model`, so the whole path (prompt building, the structured
output parse, the number guardrail, cost accounting) runs for real; only the model's words are
scripted. The script is chosen by the `action_id` both callers put in the run's metadata.

The scripted wording holds no digits and no number words, so the number guardrail always passes on
the real measured facts: the numbers a reviewer sees come from `actions.impact` (code counts, the
LLM explains). Nothing here imports a provider or reads a key.
"""

from collections.abc import Mapping
from typing import Any, Final

from langchain_core.messages import AIMessage

from app.services.impact import ImpactReport
from app.services.judge import JudgeVerdict

# Fake token usage: enough for `llm_cost_usd` to be non-zero from LLM_PRICES. Demo data.
FAKE_INPUT_TOKENS: Final = 1200
FAKE_OUTPUT_TOKENS: Final = 150

DEFAULT_REPORT: Final = ImpactReport(
    headline="Changes company data as the statement describes.",
    key_points=["The measured changes are listed in the impact table."],
    concerns=["Check the affected tables before approving."],
)
DEFAULT_VERDICT: Final = JudgeVerdict(
    recommendation="review",
    risk_level="medium",
    confidence=0.8,
    reason="A person should look at this change before it is applied.",
)


class ScriptedStructured:
    """What `with_structured_output(schema, include_raw=True)` returns: `ainvoke` gives
    `{"raw", "parsed", "parsing_error"}` with the scripted answer for the action in `config`."""

    def __init__(self, scripts: Mapping[str, Any], default: Any) -> None:
        self._scripts = scripts
        self._default = default

    async def ainvoke(self, messages: object, config: Mapping[str, Any] | None = None) -> dict:
        action_id = str(((config or {}).get("metadata") or {}).get("action_id", ""))
        parsed = self._scripts.get(action_id, self._default)
        raw = AIMessage(
            content="",
            usage_metadata={
                "input_tokens": FAKE_INPUT_TOKENS,
                "output_tokens": FAKE_OUTPUT_TOKENS,
                "total_tokens": FAKE_INPUT_TOKENS + FAKE_OUTPUT_TOKENS,
            },
        )
        return {"raw": raw, "parsed": parsed, "parsing_error": None}


class ScriptedModel:
    def __init__(self, reports: Mapping[str, ImpactReport], verdicts: Mapping[str, JudgeVerdict]):
        self._reports = reports
        self._verdicts = verdicts

    def with_structured_output(self, schema: type, *, include_raw: bool = False) -> Any:
        assert include_raw, "the engines always ask for the raw message (for the token count)"
        if schema is ImpactReport:
            return ScriptedStructured(self._reports, DEFAULT_REPORT)
        if schema is JudgeVerdict:
            return ScriptedStructured(self._verdicts, DEFAULT_VERDICT)
        raise TypeError(f"no script for {schema.__name__}")


class ScriptedModelFactory:
    """Callable like `build_chat_model(model, options, settings)`. Scripts are added per action."""

    def __init__(self) -> None:
        self._reports: dict[str, ImpactReport] = {}
        self._verdicts: dict[str, JudgeVerdict] = {}

    def script(
        self,
        action_id: object,
        *,
        report: ImpactReport | None = None,
        verdict: JudgeVerdict | None = None,
    ) -> None:
        if report is not None:
            self._reports[str(action_id)] = report
        if verdict is not None:
            self._verdicts[str(action_id)] = verdict

    def __call__(self, model: str, options: Mapping[str, Any], settings: object) -> ScriptedModel:
        return ScriptedModel(self._reports, self._verdicts)
