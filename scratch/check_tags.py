
import re

with open('d:/Face Recog Firebase/templates/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

# Simple tag balance check
open_divs = len(re.findall(r'<div', content))
close_divs = len(re.findall(r'</div>', content))

print(f"Open DIVs: {open_divs}")
print(f"Close DIVs: {close_divs}")

# Check specific sections
# Faculty Registration View starts at line 125
# Dashboard View starts at line 215

lines = content.split('\n')
for i, line in enumerate(lines):
    if 'id="faculty-registration-view"' in line:
        print(f"Faculty view starts at index {i+1}")
    if 'id="dashboard-view"' in line:
        print(f"Dashboard view starts at index {i+1}")
