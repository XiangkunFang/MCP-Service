#!/bin/bash
set -e
BASE=/root/PersonalProject/MCP-Service
cd "$BASE/gdrive-mcp" && docker compose up -d
cd "$BASE/gcal-mcp" && docker compose up -d
