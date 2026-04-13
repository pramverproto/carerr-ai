from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    db_host: str = "127.0.0.1"
    db_port: int = 3306
    db_user: str = "root"
    db_password: str = ""
    db_name: str = "career"
    db_pool_min: int = 1
    db_pool_max: int = 5

    app_host: str = "0.0.0.0"
    app_port: int = 8001


settings = Settings()
