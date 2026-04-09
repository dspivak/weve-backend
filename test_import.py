import sys
import os
sys.path.append(os.getcwd())
try:
    from app.routers import chat
    print("Import success")
except Exception as e:
    print(f"Import failed: {e}")
    import traceback
    traceback.print_exc()
