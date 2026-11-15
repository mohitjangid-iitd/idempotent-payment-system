from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    db_name: str
    db_user: str
    db_password: str
    db_port: int
    db_host: str
    log_level: str = "INFO"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @property
    def database_url(self) -> str:
        return f"postgresql+asyncpg://{self.db_user}:{self.db_password}@{self.db_host}:{self.db_port}/{self.db_name}"

settings = Settings()
