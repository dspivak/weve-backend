from app.supabase_client import get_supabase_admin

admin = get_supabase_admin()
if admin:
    resp = admin.table("post_likes").select("post_id", count="exact").limit(1).execute()
    print("COUNT:", resp.count)
    print("DATA:", resp.data)
else:
    print("NO ADMIN")
