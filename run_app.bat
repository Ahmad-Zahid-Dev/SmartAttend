@echo off
setlocal
set "ROOT=%~dp0"

python "%ROOT%start_app.py" %*

endlocal
