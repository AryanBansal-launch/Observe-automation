#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_DIR="$SCRIPT_DIR"
# Agent sees this directory; set AGENT_WORKSPACE to a parent path to include folders outside this repo
AGENT_WORKSPACE="${AGENT_WORKSPACE:-../}"

LOG_FILE_NAME="output/error_report.txt"
LOG_PATH="$LOG_FILE_NAME"
ERROR_FILE=""

# Load .env first so AGENT_MODEL can be overridden by CLI
if [[ -f "$WORKSPACE_DIR/.env" ]]; then
  set -a
  # shellcheck source=/dev/null
  . "$WORKSPACE_DIR/.env"
  set +a
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --error-file)
      ERROR_FILE="$2"
      shift 2
      ;;
    --model)
      AGENT_MODEL="$2"
      shift 2
      ;;
    *)
      echo "Unknown option: $1"
      echo "Usage: $0 [--error-file <path>] [--model <model-name>]"
      exit 1
      ;;
  esac
done

if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="python"
else
  echo "Error: python is not installed or not in PATH."
  exit 1
fi

if ! command -v agent >/dev/null 2>&1; then
  echo "Error: Cursor CLI 'agent' command is not available in PATH."
  exit 1
fi

if [[ -n "$ERROR_FILE" ]]; then
  # Use provided single-error file (e.g. from Fix button)
  if [[ -f "$WORKSPACE_DIR/$ERROR_FILE" ]]; then
    LOG_PATH="$ERROR_FILE"
    LOG_PATH_ABS="$WORKSPACE_DIR/$ERROR_FILE"
    echo "Using error file: $LOG_PATH_ABS"
  else
    echo "Error: --error-file $ERROR_FILE not found."
    exit 1
  fi
else
  # Generate full report via extract_errors.py
  mkdir -p "$WORKSPACE_DIR/output"
  echo "Generating error log at: $LOG_PATH"
  "$PYTHON_BIN" "$WORKSPACE_DIR/extract_errors.py" -o "$LOG_PATH"

  if [[ ! -s "$LOG_PATH" ]]; then
    echo "Warning: generated log file is empty: $LOG_PATH"
  fi

  LOG_PATH_ABS="$WORKSPACE_DIR/$LOG_PATH"
fi

PROMPT="Analyze the errors in the log file at $LOG_PATH_ABS and suggest concrete fixes in the workspace codebase (workspace root: $AGENT_WORKSPACE). Reference relevant files and functions. If the error matches a known pattern (e.g. DeploymentControllerRMQ), consult docs/RUNBOOK.md for root causes and recommended fixes. Think deeply."

AGENT_OUTPUT_FILE="${AGENT_OUTPUT_FILE:-output/agent_analysis.md}"
AGENT_OUTPUT_PATH="$WORKSPACE_DIR/$AGENT_OUTPUT_FILE"

# Build agent args; use composer-1.5 by default, override with AGENT_MODEL env or --model flag
AGENT_MODEL="${AGENT_MODEL:-composer-1.5}"
AGENT_ARGS=(--mode=ask --print --trust --model "$AGENT_MODEL" --workspace "$AGENT_WORKSPACE")

echo "Starting Cursor agent in ask mode (workspace: $AGENT_WORKSPACE, output -> $AGENT_OUTPUT_PATH)..."
agent "${AGENT_ARGS[@]}" "$PROMPT" 2>&1 | tee "$AGENT_OUTPUT_PATH"
