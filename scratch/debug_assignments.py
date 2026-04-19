from database.db import DatabaseManager
from google.cloud.firestore_v1.base_query import FieldFilter

db = DatabaseManager()

# Find teacher named Tariq Sagheer
teachers = db.db.collection('teachers').where(filter=FieldFilter('Name', '==', 'Tariq Sagheer')).get()
if not teachers:
    print("Teacher Tariq Sagheer not found")
else:
    t = teachers[0]
    tid = t.id
    print(f"Teacher found: {tid} ({t.to_dict().get('Username')})")
    
    # Check assignments
    assigns = db.db.collection('assignments').where(filter=FieldFilter('TeacherId', '==', tid)).get()
    print(f"Total assignments found: {len(assigns)}")
    for a in assigns:
        print(f"  - Class: {a.to_dict().get('ClassId')}, Subject: {a.to_dict().get('SubjectId')}")

# Check all classes to see their ShortNames
classes = db.db.collection('classes').get()
print("\nAvailable Classes:")
for c in classes:
    d = c.to_dict()
    print(f"  - {c.id}: {d.get('ClassName')} ({d.get('ShortName')})")
