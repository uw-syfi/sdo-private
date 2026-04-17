#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

CLAUDE_BIN="${CLAUDE_BIN:-/home/shli/.local/bin/claude}"
MODEL="${MODEL:-claude-sonnet-4-6}"
NAMESPACE="${NAMESPACE:-social-network}"
STAGE="${STAGE:-diagnosis}"
ITERATION="${ITERATION:-1}"
PROMPT_VERSION="${PROMPT_VERSION:-v3}"
EXP_DIR="${EXP_DIR:-$REPO_ROOT/bench/sregym/exp_env/exp_env_0}"
SHARED_FILE="${SHARED_FILE:-$EXP_DIR/diagnosis_session_state.md}"
RESULT_FILE="${RESULT_FILE:-$REPO_ROOT/bench/sregym/.local_tmp/manual_crucible_result.json}"

cd "$EXP_DIR"

PROMPT="${PROMPT:-List all available MCP tool names exactly as exposed in this session. Do not call any tools yet.}"

SETTINGS_JSON=$(cat <<EOF
{
  "sandbox": {
    "enabled": true,
    "failIfUnavailable": true,
    "autoAllowBashIfSandboxed": true,
    "allowUnsandboxedCommands": false,
    "excludedCommands": ["kubectl *", "curl *", "wget *"],
    "filesystem": {
      "allowWrite": ["/home/shli/.kube"],
      "allowRead": [
        "$EXP_DIR",
        "/bin", "/sbin", "/usr", "/lib", "/lib64", "/etc", "/opt", "/var",
        "/proc", "/sys", "/dev", "/tmp", "/run",
        "$REPO_ROOT/bench/sregym/.local_tmp"
      ],
      "denyRead": ["/"]
    }
  },
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Read|Glob|Grep|Edit|Write|NotebookEdit",
        "hooks": [
          {
            "type": "command",
            "command": "\"$REPO_ROOT/libs/agent_cli/hooks/confine_reads.py\" \"$EXP_DIR\""
          }
        ]
      }
    ]
  }
}
EOF
)

MCP_CONFIG_JSON=$(cat <<EOF
{
  "mcpServers": {
    "crucible-tools": {
      "command": "uv",
      "args": [
        "--directory",
        "$REPO_ROOT",
        "run",
        "python",
        "-m",
        "sregym_agents.crucible.tools.mcp_server",
        "--tools",
        "sre",
        "--namespace",
        "$NAMESPACE",
        "--stage",
        "$STAGE",
        "--shared-file",
        "$SHARED_FILE",
        "--model",
        "$MODEL",
        "--iteration",
        "$ITERATION",
        "--prompt-version",
        "$PROMPT_VERSION",
        "--ltm-call-budget",
        "1",
        "--enable-ltm-verified-direct-submit",
        "--result-file",
        "$RESULT_FILE",
        "--backend",
        "agent-cli",
        "--provider",
        "claude"
      ]
    }
  }
}
EOF
)

SYSTEM_PROMPT=$(cat <<EOF
<system>
You are an expert SRE diagnosing issues in a Kubernetes application. Identify the root cause.
</system>

$PROMPT
EOF
)

exec "$CLAUDE_BIN" \
  --dangerously-skip-permissions \
  --model "$MODEL" \
  --mcp-config "$MCP_CONFIG_JSON" \
  --strict-mcp-config \
  --settings "$SETTINGS_JSON" \
  "$SYSTEM_PROMPT"
