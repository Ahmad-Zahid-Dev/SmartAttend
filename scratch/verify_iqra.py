
from database.db import db
from google.cloud.firestore_v1.base_query import FieldFilter

def check():
    docs = db.db.collection('students').where(filter=FieldFilter('FullName', '==', 'Iqra Khan')).get()
    for doc in docs:
        print(f"ID: {doc.id}")
        data = doc.to_dict()
        for k, v in data.items():
            print(f"  {k}: {v}")

if __name__ == "__main__":
    check()
