from database.db import db

classes = db.get_classes()
for c in classes:
    print(f"ID: {c['id']}, Name: {c['ClassName']}")
