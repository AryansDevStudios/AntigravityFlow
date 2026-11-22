@echo off
title AntigravityFlow Background Daemon
cd /d "%~dp0"
echo Starting AntigravityFlow...
"C:\Program Files\Python314\python.exe" daemon.py
pause
