import os

def patch_file(path, old, new):
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
    if old in content:
        new_content = content.replace(old, new)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        print(f"Patched {os.path.basename(path)}")
    else:
        print(f"Could not find target in {os.path.basename(path)}")

# Patch index.html
patch_file(r'd:\Face Recog Firebase\templates\index.html', 
           'Save Registration', 
           'Confirm Faculty Registration')

# Patch main.js
old_filter = '        // Filter: Keep only subjects with a standard alphanumeric code\n        subjects = subjects.filter(s => s.SubjectCode && /^[a-zA-Z]+\d+$/.test(s.SubjectCode.trim()));'
new_filter = """        // Filter: Only show subjects with specific course prefixes (BCS, MCA, BTech, MTech)
        const allowed = ['BCS', 'MCA', 'BTech', 'MTech'];
        subjects = subjects.filter(s => {
            if(!s.SubjectCode) return false;
            const code = s.SubjectCode.toUpperCase();
            return allowed.some(course => code.includes(course.toUpperCase()));
        });"""

patch_file(r'd:\Face Recog Firebase\static\js\main.js', old_filter, new_filter)
