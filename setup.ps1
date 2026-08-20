# Convenience wrapper: run the cross-platform setup with the system python.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
python "$root\setup.py" @args
exit $LASTEXITCODE
