from database.db import db

subjects = db.get_subjects()
print(f"Total Subjects: {len(subjects)}")
for s in subjects[:5]:
    print(f"- {s.get('SubjectName')} ({s.get('SubjectCode')})")
