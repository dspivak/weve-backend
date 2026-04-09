from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""  # optional; used to insert into public.profiles on signup
    # Set to true for testing: create user with email already confirmed (no verification email). Requires service_role key.
    signup_auto_confirm_email: bool = False
    frontend_url: str = "http://localhost:3000"
    openai_api_key: str = ""

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
