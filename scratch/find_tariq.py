import os
import sys
sys.path.append(os.getcwd())

from database.db import DatabaseManager
db = DatabaseManager()
ts = db.db.collection('teachers').get()
for t in ts:
    print(f'ID: {t.id}, Name: {t.to_dict().get("FullName")}')
