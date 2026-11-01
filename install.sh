#!/usr/bin/env bash
# G2 Gym — 1-Click Silent Service Installer (Git Bash / WSL / Linux)
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"

if command -v cmd.exe >/dev/null 2>&1; then
    # Running under Windows Git Bash / MSYS
    cmd.exe /c "cd /d \"$DIR\" && install_silent_service.bat"
else
    # Running under pure Linux / Unix
    echo "Starting zk_server in background..."
    nohup python3 "$DIR/zk_server.py" > "$DIR/zk_server.log" 2>&1 &
    echo "Running in background (PID: $!)"
fi
