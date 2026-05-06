@echo off
chcp 65001 >nul
rem Move to skill root (parent of scripts/) so PROJECT_ROOT resolves sensibly.
cd /d %~dp0..
title ASCII Draw Server
py scripts\server.py
pause
