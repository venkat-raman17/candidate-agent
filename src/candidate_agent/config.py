from typing import Optional

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # MCP server
    mcp_server_url: str = "http://localhost:8081/mcp"
    mcp_connect_timeout: int = 30

    # LLM — Anthropic (used when LOCAL_LLM=false)
    anthropic_api_key: Optional[SecretStr] = None
    llm_model: str = "claude-sonnet-4-6"
    llm_temperature: float = 0.0

    # LLM — Local (used when LOCAL_LLM=true)
    # Works with any OpenAI-compatible server: Ollama, LM Studio, vLLM, etc.
    local_llm: bool = False
    local_llm_base_url: str = "http://localhost:11434/v1"  # Ollama default
    local_llm_model: str = "llama3.2"
    local_llm_api_key: str = "ollama"  # Ollama ignores it; set for vLLM/LM Studio auth

    # FastAPI
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    log_level: str = "INFO"

    @model_validator(mode="after")
    def _check_api_key(self) -> "Settings":
        if not self.local_llm and self.anthropic_api_key is None:
            raise ValueError(
                "ANTHROPIC_API_KEY is required when LOCAL_LLM is false. "
                "Set LOCAL_LLM=true to use a local LLM instead."
            )
        return self


# Module-level singleton — imported by other modules
settings = Settings()
