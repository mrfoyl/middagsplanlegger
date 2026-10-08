#!/usr/bin/env bash
# Lagre Oda-innlogging i ~/.config/middag/oda.env (rettighet 600, utenfor repoet).
# Passordet leses skjult og havner aldri i shell-historikken.
set -euo pipefail
FIL="${MIDDAG_CREDENTIALS:-$HOME/.config/middag/oda.env}"
mkdir -p "$(dirname "$FIL")"
chmod 700 "$(dirname "$FIL")"
read -rp "Oda e-post: " EPOST
read -rsp "Oda passord: " PASSORD; echo
umask 077
printf 'ODA_EMAIL=%s\nODA_PASSWORD=%s\n' "$EPOST" "$PASSORD" > "$FIL"
chmod 600 "$FIL"
echo "Lagret i $FIL (600). Test med: python3 middag.py oda login"
