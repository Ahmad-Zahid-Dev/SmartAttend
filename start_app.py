import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
REQUIREMENTS_FILE = PROJECT_ROOT / "requirements.txt"
APP_FILE = PROJECT_ROOT / "app.py"
REQUIRED_IMPORTS = [
    "cv2",
    "numpy",
    "flask",
    "firebase_admin",
    "pandas",
    "openpyxl",
]


def pick_python() -> str:
    """Use system/default interpreter so VS Code Run UI behaves like normal folder runs."""
    return "python"


def run(cmd: list[str], check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=str(PROJECT_ROOT),
        check=check,
        text=True,
        capture_output=capture,
    )


def ensure_pip(py: str) -> None:
    try:
        run([py, "-m", "pip", "--version"], check=True)
    except Exception:
        run([py, "-m", "ensurepip", "--upgrade"], check=True)


def get_missing_modules(py: str) -> list[str]:
    code = (
        "import importlib.util, json; "
        f"mods={json.dumps(REQUIRED_IMPORTS)}; "
        "missing=[m for m in mods if importlib.util.find_spec(m) is None]; "
        "print(json.dumps(missing))"
    )
    result = run([py, "-c", code], check=True, capture=True)
    output = (result.stdout or "[]").strip()
    try:
        return json.loads(output)
    except Exception:
        return REQUIRED_IMPORTS


def ensure_dependencies(py: str) -> None:
    if not REQUIREMENTS_FILE.exists():
        print("[WARN] requirements.txt not found. Skipping dependency install.")
        return

    missing = get_missing_modules(py)
    if not missing:
        print("[OK] All required modules already installed.")
        return

    print(f"[INFO] Missing modules detected: {', '.join(missing)}")
    print("[INFO] Installing dependencies from requirements.txt ...")
    run([py, "-m", "pip", "install", "-r", str(REQUIREMENTS_FILE)], check=True)


def start_app(py: str, app_args: list[str]) -> int:
    if not APP_FILE.exists():
        print("[ERROR] app.py not found in project root.")
        return 1

    cmd = [py, str(APP_FILE)] + app_args
    print(f"[RUN] {' '.join(cmd)}")
    completed = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    return completed.returncode


def main() -> int:
    app_args = sys.argv[1:]
    py = pick_python()

    print(f"[INFO] Project root: {PROJECT_ROOT}")
    print(f"[INFO] Using Python: {py}")

    try:
        ensure_pip(py)
        ensure_dependencies(py)
    except subprocess.CalledProcessError as exc:
        print("[ERROR] Failed while preparing environment.")
        print(str(exc))
        return 1

    return start_app(py, app_args)


if __name__ == "__main__":
    raise SystemExit(main())
