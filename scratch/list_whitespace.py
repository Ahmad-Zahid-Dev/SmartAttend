with open(r'd:\Face Recog Firebase\static\js\main.js', 'r', encoding='utf-8') as f:
    lines = f.readlines()
    for i, line in enumerate(lines[520:535]):
        print(f"{i+521}: {repr(line)}")
