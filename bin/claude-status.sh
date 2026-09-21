#!/usr/bin/env sh

######################################################################
# @author      : Owen Wiggins
# @file        : claude-status
# @created     : Monday May 19, 2026 00:00:00 PDT
#
# @description : Claude Code statusLine entrypoint. Renders model, effort,
#                quota, and context data for Claude's own bar.
#
#                Configure in ~/.claude/settings.json:
#                  "statusLine": {
#                    "type": "command",
#                    "command": "$HOME/bin/agent-kit/claude-status.sh",
#                    "refreshInterval": 2
#                  }
######################################################################

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
exec "$script_dir/agent-status" claude
