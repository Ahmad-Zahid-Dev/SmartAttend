"""
Quick login diagnostic — run this standalone to see exactly why login is failing.
Usage: python debug_login.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

try:
    from database.db import DatabaseManager
    print("[OK] DatabaseManager imported")
except Exception as e:
    print(f"[FAIL] Import error: {e}")
    sys.exit(1)

try:
    db = DatabaseManager()
    print("[OK] Firebase connected")
except Exception as e:
    print(f"[FAIL] Firebase init error: {e}")
    sys.exit(1)

print("\n--- Checking 'teachers' collection ---")
try:
    docs = db.db.collection('teachers').limit(10).get()
    if not docs:
        print("[WARN] No teachers found in Firestore!")
    for d in docs:
        data = d.to_dict()
        print(f"  ID={d.id}  Username={data.get('Username')}  Password={data.get('Password')}  IsActive={data.get('IsActive')}")
except Exception as e:
    print(f"[FAIL] Could not read teachers: {e}")

print("\n--- Checking 'students' collection (first 5) ---")
try:
    docs = db.db.collection('students').limit(5).get()
    if not docs:
        print("[WARN] No students found in Firestore!")
    for d in docs:
        data = d.to_dict()
        print(f"  ID={d.id}  Username={data.get('Username')}  Password={data.get('Password')}  IsActive={data.get('IsActive')}")
except Exception as e:
    print(f"[FAIL] Could not read students: {e}")

print("\n--- Testing Login: admin / admin123 ---")
try:
    user = db.get_user('admin', 'admin123', role='admin')
    if user:
        print(f"[SUCCESS] Admin login works! Role={user.get('Role')}, Name={user.get('Name')}")
    else:
        print("[FAIL] get_user() returned None for admin/admin123")
except Exception as e:
    print(f"[FAIL] Login test threw exception: {e}")

print("\n--- Done ---")
