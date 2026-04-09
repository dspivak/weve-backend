import os
from dotenv import load_dotenv
load_dotenv(".env")
from app.supabase_client import get_supabase_admin
import datetime

admin = get_supabase_admin()
print(admin.table("conversations").select("id, last_read_at_1, last_read_at_2").limit(1).execute())

now_iso = datetime.datetime.utcnow().isoformat()
convs = admin.table("conversations").select("id").limit(1).execute()
if convs.data:
    cid = convs.data[0]['id']
    try:
        res = admin.table("conversations").update({"last_read_at_1": now_iso}).eq("id", cid).execute()
        print("Update Success:", res.data)
    except Exception as e:
        print("Update Failed:", e)
