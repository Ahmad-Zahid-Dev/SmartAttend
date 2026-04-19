from database.db import db
import time

print("Testing DB connection...")
try:
    classes = db.get_classes()
    print(f"Found {len(classes)} classes.")
    
    print("Testing student ID generation...")
    uid = db._get_next_id('students')
    print(f"Next Student ID: {uid}")
    
except Exception as e:
    print(f"Database Error: {e}")
