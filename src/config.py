from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    host: str = "127.0.0.1"
    port: int = 7781
    log_level: str = "info"
    public_base_url: str = "http://127.0.0.1:7781"
    # SQLite path for chess game persistence. Empty disables persistence.
    chess_db_path: str = ""
    # SQLite path for shared accounts/leaderboards. Empty → in-memory ephemeral.
    accounts_db_path: str = ""

    # Discord OAuth (optional login for leaderboards). Empty client id disables it.
    web_base_url: str = "http://127.0.0.1:7781"
    discord_oauth_client_id: str = ""
    discord_oauth_client_secret: str = ""
    discord_oauth_redirect_uri: str = ""  # default: {web_base_url}/auth/discord/callback
    session_secret: str = "dev-secret-change-me"


settings = Settings()
