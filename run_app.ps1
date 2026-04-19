$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$starter = Join-Path $root "start_app.py"

python $starter @args
