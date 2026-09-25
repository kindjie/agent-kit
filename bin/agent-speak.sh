#!/usr/bin/env sh

######################################################################
# @author      : Owen Wiggins
# @file        : agent-speak
# @created     : Tuesday July 07, 2026 00:00:00 PDT
#
# @description : Spoken attention notifications for coding agents
#                (Claude Code, Codex CLI).
#
#                Usage:
#                  agent-speak.sh <message>       speak a literal message
#                  agent-speak.sh -- <message>    ... one starting with '-'
#                  agent-speak.sh --mute          silence notifications
#                  agent-speak.sh --unmute        restore notifications
#                  agent-speak.sh --toggle-mute   flip the mute state
#                  agent-speak.sh --status        print mute state; exits
#                                                 0 unmuted, 1 muted
#
#                An unrecognised option is an error, never something to
#                speak. An agent notify hook pointed here by mistake
#                sends a JSON payload; spoken, that reads the payload and
#                a fragment of the reply aloud on every completed turn.
#
#                Mute:   --mute / --unmute / --toggle-mute / --status
#                        (state file: ~/.config/agent-speak/mute)
#                Voice:  $AGENT_SPEAK_VOICE, default "Zoe (Premium)"
#                        (macOS: download Premium voices via System
#                        Settings > Accessibility > Read & Speak)
#                Volume: $AGENT_SPEAK_VOLUME, 0.0-1.0, default 0.4
######################################################################

CONF_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/agent-speak"
MUTE_FILE="$CONF_DIR/mute"
# A private per-user directory: a lock in shared /tmp could be pre-created
# or redirected by another user.
LOCK_ROOT="${XDG_RUNTIME_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}}/agent-speak"
LOCK="$LOCK_ROOT/lock"
have_lock=0
VOICE="${AGENT_SPEAK_VOICE:-Zoe (Premium)}"
VOLUME="${AGENT_SPEAK_VOLUME:-0.4}"

is_macos() { [ "$(uname)" = Darwin ]; }

muted() { [ -e "$MUTE_FILE" ]; }

mute_on() {
  mkdir -p "$CONF_DIR"
  touch "$MUTE_FILE"
  echo 'agent-speak: muted'
}

mute_off() {
  rm -f "$MUTE_FILE"
  echo 'agent-speak: unmuted'
}

toggle_mute() {
  if muted; then mute_off; else mute_on; fi
}

mute_status() {
  if muted; then
    echo 'agent-speak: muted'
    return 1
  fi
  echo 'agent-speak: unmuted'
}

lock_root_is_private() {
  (umask 077 && mkdir -p "$LOCK_ROOT") 2>/dev/null &&
    [ -d "$LOCK_ROOT" ] && [ ! -L "$LOCK_ROOT" ] && [ -O "$LOCK_ROOT" ] &&
    chmod 700 "$LOCK_ROOT" 2>/dev/null
}

# Only the holder releases, so a timed-out wait never breaks a live lock.
release_lock() {
  if [ "$have_lock" = 1 ] && [ "$(readlink "$LOCK" 2>/dev/null)" = "$$" ]; then
    rm -f "$LOCK"
  fi
  have_lock=0
}

# Serialise speech across parallel agent sessions: wait (up to ~6 s) for a
# live holder, reap a dead one, then speak anyway without the lock. The
# lock is a symlink naming the holder's PID, created atomically by ln.
acquire_lock() {
  lock_root_is_private || return 0
  tries=0
  while ! ln -s "$$" "$LOCK" 2>/dev/null; do
    holder=$(readlink "$LOCK" 2>/dev/null)
    if [ -n "$holder" ] && ! kill -0 "$holder" 2>/dev/null; then
      rm -f "$LOCK"
    fi
    tries=$((tries + 1))
    [ "$tries" -ge 12 ] && return 0
    sleep 0.5
  done
  have_lock=1
}

speak() {
  msg=$1
  [ -n "$msg" ] && ! muted || return 0
  trap release_lock EXIT INT TERM
  acquire_lock
  if is_macos; then
    # [[volm N]] is an embedded speech command scaling this utterance.
    say -v "$VOICE" -- "[[volm $VOLUME]] $msg" 2>/dev/null ||
      say -- "[[volm $VOLUME]] $msg"
  elif command -v spd-say >/dev/null 2>&1; then
    # spd-say volume range is -100..100 with 0 as normal.
    spd-say -w \
      -i "$(awk -v v="$VOLUME" 'BEGIN { printf "%d", v * 200 - 100 }')" \
      -- "$msg"
  elif command -v espeak-ng >/dev/null 2>&1; then
    # espeak-ng amplitude range is 0..200 with 100 as normal.
    espeak-ng \
      -a "$(awk -v v="$VOLUME" 'BEGIN { printf "%d", v * 200 }')" \
      "$msg" >/dev/null 2>&1
  else
    printf '\a'
  fi
  release_lock
  trap - EXIT INT TERM
}

case ${1:-} in
  --mute) mute_on ;;
  --unmute) mute_off ;;
  --toggle-mute) toggle_mute ;;
  --status) mute_status ;;
  --) shift; speak "$*" ;;
  --*)
    printf 'agent-speak: unknown option: %s\n' "$1" >&2
    exit 2
    ;;
  *) speak "$*" ;;
esac
