import os
import sys

# Add the project root to sys.path
sys.path.append(os.getcwd())

from database.db import DatabaseManager
from google.cloud.firestore_v1.base_query import FieldFilter

db_manager = DatabaseManager()
db = db_manager.db

def get_or_create_class(short_name, full_name):
    classes = db_manager.get_classes()
    for c in classes:
        if c['ShortName'].upper() == short_name.upper():
            return c['id']
    
    # Create if not found
    doc_ref = db.collection('classes').add({
        'ClassName': full_name,
        'ShortName': short_name
    })
    return doc_ref[1].id

def add_subject(class_id, code, name):
    # Check if subject exists
    subjects = db.collection('subjects').where(filter=FieldFilter('SubjectCode', '==', code)).get()
    if subjects:
        # Update
        db.collection('subjects').document(subjects[0].id).update({
            'SubjectName': name,
            'ClassId': class_id
        })
        print(f"Updated: {code} - {name}")
    else:
        # Add
        db.collection('subjects').add({
            'SubjectName': name,
            'SubjectCode': code,
            'ClassId': class_id
        })
        print(f"Added: {code} - {name}")

# Target Classes
class_targets = {
    'BCA': 'Bachelor of Computer Applications',
    'MCA': 'Master of Computer Applications',
    'BTECH': 'Bachelor of Technology',
    'MTECH': 'Master of Technology'
}

ids = {}
for short, full in class_targets.items():
    ids[short] = get_or_create_class(short, full)
    print(f"Class {short} ID: {ids[short]}")

# BCA Subjects
bca_subs = [
    ("Bca101", "Mathematics"),
    ("Bca102", "Python"),
    ("Bca103", "C++"),
    ("Bca104", "Full Stack Web Development I"),
    ("Bca105", "Database Management Systems") # Added 5th for BCA too
]

# MCA Subjects
mca_subs = [
    ("Mca201", "Computer Networks"),
    ("Mca202", "Java Programming"),
    ("Mca203", "Mathematics"),
    ("Mca204", "Internet Of Things"),
    ("Mca205", "Full Stack Web Development II")
]

# BTech Subjects (Example 5 since user said "likewise for all courses")
btech_subs = [
    ("BT101", "Engineering Physics"),
    ("BT102", "Engineering Chemistry"),
    ("BT103", "Basic Electrical Engineering"),
    ("BT104", "Programming for Problem Solving"),
    ("BT105", "Engineering Graphics")
]

# MTech Subjects
mtech_subs = [
    ("MT201", "Advanced Algorithms"),
    ("MT202", "Distributed Systems"),
    ("MT203", "Machine Learning"),
    ("MT204", "Cryptography & Network Security"),
    ("MT205", "Software Project Management")
]

for code, name in bca_subs: add_subject(ids['BCA'], code, name)
for code, name in mca_subs: add_subject(ids['MCA'], code, name)
for code, name in btech_subs: add_subject(ids['BTECH'], code, name)
for code, name in mtech_subs: add_subject(ids['MTECH'], code, name)

print("Setup Complete.")
