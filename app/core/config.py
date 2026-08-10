from typing import Optional

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    PROJECT_NAME: str = "SEO-v2 Engine"
    API_V1_STR: str = "/api/v1"

    DATABASE_URL: str = "postgresql://user:password@localhost:5432/seo_v2"
    CELERY_BROKER_URL: Optional[str] = None
    CELERY_RESULT_BACKEND: Optional[str] = None

    # Security
    API_KEY_NAME: str = "X-API-Key"
    SUPABASE_JWT_SECRET: Optional[str] = None
    SUPABASE_JWKS_URL: Optional[str] = None
    SUPABASE_JWT_AUDIENCE: Optional[str] = None
    SUPABASE_JWT_ISSUER: Optional[str] = None

    # External APIs
    OPENAI_API_KEY: Optional[str] = None
    RESEND_API_KEY: Optional[str] = None
    DASHBOARD_BASE_URL: str = "http://localhost:3000"
    VETO_TOKEN_TTL_HOURS: int = 48
    WP_ROLLBACK_TIMEOUT_SECONDS: float = 10.0
    WP_VETO_ENDPOINT_PATH: str = "/wp-json/seo-v2/v1/veto/rollback"

    # Content rewrite settings
    OPENAI_REWRITE_MODEL: str = "gpt-4o"
    OPENAI_REWRITE_MAX_INPUT_CHARS: int = 50000
    OPENAI_REWRITE_TEMPERATURE: float = 0.0
    OPENAI_REWRITE_MIN_IMS_GAIN: int = 5

    # Circuit Breaker Settings (Story 3.6)
    LLM_CIRCUIT_BREAKER_FAILURE_THRESHOLD: int = 5
    LLM_CIRCUIT_BREAKER_COOLDOWN_SECONDS: int = 300
    LLM_CIRCUIT_BREAKER_WINDOW_SECONDS: int = 600

    class Config:
        case_sensitive = True
        env_file = ".env"
        extra = "ignore"


settings = Settings()
