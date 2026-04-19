from database.db import db
from google.cloud import firestore

print("Checking most recent students...")
# Query by CreatedDate desc
docs = db.db.collection('students').order_by('CreatedDate', direction=firestore.Query.DESCENDING).limit(5).get()

for d in docs:
    data = d.to_dict()
    print(f"ID: {d.id}, Enrollment: {data.get('EnrollmentNo')}, Name: {data.get('FullName')}, Created: {data.get('CreatedDate')}")
