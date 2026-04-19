import os
import sys
sys.path.append(os.getcwd())
from database.db import DatabaseManager
db = DatabaseManager()
us = db.db.collection('users').get()
for u in us:
    data = u.to_dict()
    print(f"UID: {u.id}, Name: {data.get('FullName')}, Role: {data.get('Role')}, Email: {data.get('EmailId')}")
