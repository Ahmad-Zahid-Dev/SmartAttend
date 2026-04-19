
from database.db import DatabaseManager
import re

db = DatabaseManager()
subjects = db.get_subjects()

allowed_prefixes = ['bca', 'mca', 'btech', 'mtech', 'bt', 'mt']

print(f"Total subjects found: {len(subjects)}")

to_delete = []
for s in subjects:
    code = str(s.get('SubjectCode', '')).lower().strip()
    name = str(s.get('SubjectName', '')).lower().strip()
    
    # Check if ANY of the allowed prefixes match the START of the code
    is_allowed = False
    for p in allowed_prefixes:
        if code.startswith(p):
            is_allowed = True
            break
    
    if not is_allowed:
        to_delete.append(s)

print(f"Targeting {len(to_delete)} subjects for deletion...")

for s in to_delete:
    print(f"Deleting: {s.get('SubjectCode')} - {s.get('SubjectName')} (ID: {s['id']})")
    # Actually delete from Firestore
    db.db.collection('subjects').document(s['id']).delete()

print("Cleanup complete.")
