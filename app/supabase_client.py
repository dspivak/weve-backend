from supabase import create_client, Client

from app.config import settings

_supabase: Client | None = None
_supabase_admin: Client | None = None


def get_supabase() -> Client:
    global _supabase
    if _supabase is None:
        _supabase = create_client(settings.supabase_url, settings.supabase_anon_key)
    return _supabase


def get_supabase_admin() -> Client | None:
    """Client with service_role key; can bypass RLS. Use only for server-side ops (e.g. profile insert on signup)."""
    global _supabase_admin
    if not settings.supabase_service_role_key:
        return None
    if _supabase_admin is None:
        _supabase_admin = create_client(
            settings.supabase_url, settings.supabase_service_role_key
        )
    return _supabase_admin
