"""Central application configuration.

All tunables are read from environment variables (with sane defaults) so the
same codebase runs unmodified in local dev, tests, and Docker.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- App / auth ---
    app_name: str = "Enterprise AI Chat Assistant"
    environment: str = "development"
    jwt_secret_key: str = "CHANGE_ME_INSECURE_DEV_ONLY_SECRET_KEY_1234567890"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 120

    # --- LLM provider ---
    # "anthropic" | "openai" | "gemini" | "offline"
    llm_provider: Literal["anthropic", "openai", "gemini", "offline"] = "offline"
    llm_model: str = ""
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    google_api_key: str = ""
    llm_temperature: float = 0.0
    llm_timeout_seconds: float = 30.0
    llm_max_retries: int = 2

    # --- Storage paths ---
    data_dir: Path = BASE_DIR / "data"
    sql_db_path: Path = BASE_DIR / "data" / "app.db"
    vector_db_path: Path = BASE_DIR / "data" / "chroma"
    knowledge_base_dir: Path = BASE_DIR / "knowledge_base"

    # --- RAG ---
    rag_chunk_size: int = 800
    rag_chunk_overlap: int = 120
    rag_top_k: int = 4
    rag_score_threshold: float = 0.35  # below this similarity, treat as "no answer found"

    # --- Agent / safety ---
    agent_max_steps: int = 8
    agent_step_timeout_seconds: float = 25.0
    tool_call_timeout_seconds: float = 15.0

    @property
    def sqlalchemy_database_url(self) -> str:
        return f"sqlite:///{self.sql_db_path.as_posix()}"


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
