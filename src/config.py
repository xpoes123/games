from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    host: str = "127.0.0.1"
    port: int = 7781
    log_level: str = "info"
    public_base_url: str = "http://127.0.0.1:7781"


settings = Settings()
