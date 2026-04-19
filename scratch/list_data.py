import os
import sys

# Add the project root to sys.path
sys.path.append(os.getcwd())

from database.db import DatabaseManager

db = DatabaseManager()

print("--- CLASSES ---")
classes = db.get_classes()
for c in classes:
    print(f"ID: {c['id']}, Name: {c['ClassName']}, Short: {c['ShortName']}")

print("\n--- SUBJECTS ---")
subjects = db.get_subjects()
for s in subjects:
    print(f"ID: {s['id']}, Name: {s['SubjectName']}, Code: {s['SubjectCode']}")
print("\n--- ASSIGNMENTS ---")
assigns = db.db.collection('assignments').get()
for a in assigns:
    da = a.to_dict()
    print(f"Teacher: {da.get('TeacherId')}, Class: {da.get('ClassId')}, Subject: {da.get('SubjectId')}")
