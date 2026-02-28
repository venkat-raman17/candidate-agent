"""LLM factory — returns the appropriate ChatModel based on settings.

  LOCAL_LLM=false (default)  →  ChatAnthropic  (requires ANTHROPIC_API_KEY)
  LOCAL_LLM=true             →  ChatOpenAI with a custom base_url
                                 Works with any OpenAI-compatible server:
                                   Ollama     http://localhost:11434/v1
                                   LM Studio  http://localhost:1234/v1
                                   vLLM       http://localhost:8080/v1
"""

from langchain_core.language_models import BaseChatModel

from candidate_agent.config import Settings


def build_llm(settings: Settings) -> BaseChatModel:
    """Return a configured chat model from settings."""
    if settings.local_llm:
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=settings.local_llm_model,
            temperature=settings.llm_temperature,
            base_url=settings.local_llm_base_url,
            api_key=settings.local_llm_api_key,
        )

    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(
        model=settings.llm_model,
        temperature=settings.llm_temperature,
        api_key=settings.anthropic_api_key.get_secret_value(),  # type: ignore[union-attr]
    )
