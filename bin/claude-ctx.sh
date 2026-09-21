#!/usr/bin/env sh

######################################################################
# @author      : Owen Wiggins
# @file        : claude-ctx
# @created     : Monday May 19, 2026 00:00:00 PDT
#
# @description : Compatibility entrypoint for the combined Claude Code
#                and Codex tmux status component.
#
#                Usage (from tmux.conf):
#                  #(claude-ctx.sh #{window_id} #{client_width})
######################################################################

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
exec "$script_dir/agent-status" tmux "$@"
