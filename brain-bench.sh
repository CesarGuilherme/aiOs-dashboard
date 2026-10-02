#!/bin/bash
# brain-bench.sh — second brain vs no-brain retrieval benchmark.
# Runs 3 real workspace questions through two headless Claude sessions each:
#   brain    = default session (CLAUDE.md routes via ~/.claude/memory/INDEX.md)
#   baseline = same session, banned from ~/.claude (workspace/dotfiles only)
# Prints a comparison table. Requires: claude CLI, jq. Costs ~6 sonnet sessions.
#
# Usage: ./brain-bench.sh [model]   (default: sonnet)
set -u
MODEL="${1:-sonnet}"
OUT="$(mktemp -d /tmp/brain-bench.XXXXXX)"
DEV=/Volumes/SSD_CESAR/Developer

Q1="What terminal emulator, prompt tool, and color theme does César use on this machine, and where is the terminal config file?"
Q2="What is the IP address of César's n8n automation server, what is the name of the docker container it runs in, and where are the SSH credentials stored?"
Q3='In this project, chat SQL queries sometimes start failing with {"error":"certificate signature failure"}. What is the root cause and what is the exact fix procedure?'
BAN="IMPORTANT CONSTRAINT: you must NOT read anything under ~/.claude (no memory files, no INDEX.md, no CLAUDE.md there). Answer only from the workspace, repos and dotfiles. Question: "

# Expected ground truth (from the memory files) for manual correctness check:
#   Q1: WezTerm (~/.wezterm.lua), Starship, JARVIS cyan-on-navy theme
#   Q2: 172.25.12.207, container n8n-app, credentials in Keychain (brain secret list)
#   Q3: OCI rotates MySQL_Endpoint_CA; re-pin PEM+fingerprint in lib/mysql.ts via openssl s_client

run() { # name cwd prompt
  (cd "$2" && claude -p "$3" --model "$MODEL" --output-format json --max-turns 25 > "$OUT/$1.json" 2>"$OUT/$1.err")
}
echo "Running 6 sessions in parallel (model=$MODEL)..."
run q1_brain "$DEV" "$Q1" &
run q1_base  "$DEV" "$BAN$Q1" &
run q2_brain "$DEV" "$Q2" &
run q2_base  "$DEV" "$BAN$Q2" &
run q3_brain "$DEV/Secom/jarvis_ui" "$Q3" &
run q3_base  "$DEV/Secom/jarvis_ui" "$BAN$Q3" &
wait

printf "\n%-10s %6s %10s %8s %6s %7s\n" run time_s tokens_in tok_out turns cost
for f in q1_brain q1_base q2_brain q2_base q3_brain q3_base; do
  jq -r --arg n "$f" '[$n, (.duration_ms/1000|round),
    (.usage.input_tokens + .usage.cache_creation_input_tokens + .usage.cache_read_input_tokens),
    .usage.output_tokens, .num_turns, ("$" + (.total_cost_usd*100|round/100|tostring))] | @tsv' \
    "$OUT/$f.json" 2>/dev/null | awk -F'\t' '{printf "%-10s %6s %10s %8s %6s %7s\n",$1,$2,$3,$4,$5,$6}' \
    || echo "$f FAILED ($(head -c 80 "$OUT/$f.err"))"
done
echo
echo "Answers saved in $OUT/*.json — check .result against the ground truth in this script's comments."
