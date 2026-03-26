import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings
from pydantic import Field, field_validator

# Load .env from project root
load_dotenv(dotenv_path=Path(__file__).parent / ".env")


class Settings(BaseSettings):
    adspower_api_url: str = Field(
        default="http://local.adspower.net:50325",
        description="AdsPower Local API base URL",
    )
    adspower_profile_id: str = Field(
        default="your_profile_id_here",
        description="AdsPower browser profile ID",
    )
    adspower_api_key: str = Field(
        default="",
        description="AdsPower Global Local API key (leave empty for old AdsPower)",
    )
    openclaw_api_key: str = Field(
        default="your_api_key",
        description="OpenClaw / OpenAI API key",
    )

    @field_validator("adspower_profile_id")
    @classmethod
    def profile_id_must_be_set(cls, v: str) -> str:
        if v == "your_profile_id_here" or not v.strip():
            raise ValueError(
                "ADSPOWER_PROFILE_ID is not configured. "
                "Please set it in your .env file."
            )
        return v

    @field_validator("openclaw_api_key")
    @classmethod
    def api_key_check(cls, v: str) -> str:
        # API key is optional for proxy mode (OpenClaw controls directly)
        # Only required for AI mode (/command endpoint)
        return v

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
    }


def get_settings() -> Settings:
    """Load and validate settings from environment variables."""
    return Settings()


if __name__ == "__main__":
    try:
        settings = get_settings()
        print("✅ Configuration loaded successfully:")
        print(f"   AdsPower API URL : {settings.adspower_api_url}")
        print(f"   Profile ID       : {settings.adspower_profile_id}")
        print(f"   AdsPower API Key : {'***' + settings.adspower_api_key[-4:] if settings.adspower_api_key else '(not set)'}")
        print(f"   OpenClaw API Key : {settings.openclaw_api_key[:8]}...")
    except Exception as e:
        print(f"❌ Configuration error: {e}")
