import firebase_admin
from firebase_admin import credentials, firestore
import os

def add_new_classes():
    # Check if already initialized
    if not firebase_admin._apps:
        key_path = os.path.join(os.getcwd(), 'serviceAccountKey.json')
        if os.path.exists(key_path):
            cred = credentials.Certificate(key_path)
            firebase_admin.initialize_app(cred)
        else:
            print("Error: serviceAccountKey.json not found")
            return

    db = firestore.client()
    classes_ref = db.collection('classes')
    
    new_classes = [
        ('Bachelor of Technology', 'BTech'),
        ('Master of Technology', 'MTech')
    ]
    
    for name, short in new_classes:
        # Check if already exists
        existing = classes_ref.where('ShortName', '==', short).limit(1).get()
        if not existing:
            classes_ref.add({
                'ClassName': name,
                'ShortName': short,
                'IsActive': True,
                'CreatedDate': firestore.SERVER_TIMESTAMP
            })
            print(f"Added: {name} ({short})")
        else:
            print(f"Already exists: {short}")

if __name__ == "__main__":
    add_new_classes()
