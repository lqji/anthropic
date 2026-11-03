from clinic.config import Settings
from clinic.llm.fake import FakeLLM, ScriptedFakeLLM
from clinic.llm.openai_adapter import OpenAILLM
from clinic.llm.port import AnswerResult, ClassifyResult, Intent, LLMPort, Passage

__all__ = [
    "AnswerResult",
    "ClassifyResult",
    "FakeLLM",
    "Intent",
    "LLMPort",
    "OpenAILLM",
    "Passage",
    "ScriptedFakeLLM",
    "make_llm",
]


def make_llm(settings: Settings) -> LLMPort:
    """FakeLLM for LLM_PROVIDER=fake, otherwise the OpenAI adapter."""
    if settings.llm_provider == "fake":
        return FakeLLM()
    return OpenAILLM(settings)
