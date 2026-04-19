import os
import sys
sys.path.append(os.getcwd())
from database.db import DatabaseManager
db = DatabaseManager()
ss = db.db.collection('subjects').where('SubjectCode', '==', 'Bca105').get()
for s in ss:
    print(f'ID: {s.id}, Name: {s.to_dict().get("SubjectName")}')
