"""
Simple web app for Observe dashboard check.
Serves a frontend and runs extract_errors.py with user-provided env vars and options.
"""
import json
import os
import subprocess
import tempfile
from pathlib import Path

from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__, static_folder="static")
SCRIPT_DIR = Path(__file__).resolve().parent
SERVICES_CONFIG = SCRIPT_DIR / "services.sample.json"


def load_services():
    """Load services list from services.sample.json. Returns list of dicts or []."""
    if not SERVICES_CONFIG.is_file():
        return []
    with open(SERVICES_CONFIG, "r") as f:
        data = json.load(f)
    return data if isinstance(data, list) else [data]


def run_dashboard_check(env_vars, options):
    """
    Run extract_errors.py in a subprocess with given env and options.
    Returns (success: bool, report_text: str, error_message: str|None).
    """
    # Required
    customer_id = (env_vars or {}).get("OBSERVE_CUSTOMER_ID", "").strip()
    api_key = (env_vars or {}).get("OBSERVE_API_KEY", "").strip()
    if not customer_id or not api_key:
        return False, "", "OBSERVE_CUSTOMER_ID and OBSERVE_API_KEY are required."

    env = os.environ.copy()
    env["OBSERVE_CUSTOMER_ID"] = customer_id
    env["OBSERVE_API_KEY"] = api_key
    for key in ("OBSERVE_CLUSTER", "OBSERVE_WORKSPACE_ID", "OBSERVE_DATASET_ID", "REGION", "START_IST", "END_IST"):
        val = (env_vars or {}).get(key)
        if val is not None and str(val).strip():
            env[key] = str(val).strip()

    cmd = [os.environ.get("python3", "python3"), str(SCRIPT_DIR / "extract_errors.py")]

    # Single service: resolve from services.sample.json by name
    selected_service = (options.get("service") or "").strip()
    if selected_service:
        services = load_services()
        svc = next((s for s in services if (s.get("name") or s.get("service_name", "")) == selected_service), None)
        if not svc:
            return False, "", f"Unknown service: {selected_service!r}. Check services.sample.json."
        cmd.extend(["--workspace", str(svc.get("workspace_id", ""))])
        cmd.extend(["--dataset", str(svc.get("dataset_id", ""))])
        pipeline_file = svc.get("pipeline_file")
        if pipeline_file:
            path = Path(pipeline_file)
            full_path = path if path.is_absolute() else (SCRIPT_DIR / pipeline_file)
            cmd.extend(["--pipeline-file", str(full_path)])
    else:
        if options.get("workspace"):
            cmd.extend(["--workspace", str(options["workspace"])])
        if options.get("dataset"):
            cmd.extend(["--dataset", str(options["dataset"])])

    if options.get("start"):
        cmd.extend(["--start", str(options["start"])])
    if options.get("end"):
        cmd.extend(["--end", str(options["end"])])
    if options.get("all_services"):
        cmd.append("--all-services")
    if options.get("all_regions"):
        cmd.append("--all-regions")
    if options.get("auto"):
        cmd.append("--auto")
    if options.get("config_path"):
        cmd.extend(["--config", str(options["config_path"])])

    fd, out_path = tempfile.mkstemp(suffix=".txt", prefix="observe_report_")
    os.close(fd)
    try:
        cmd.extend(["--output", out_path])
        result = subprocess.run(
            cmd,
            cwd=str(SCRIPT_DIR),
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        report = ""
        if os.path.isfile(out_path):
            with open(out_path, "r") as f:
                report = f.read()
            os.unlink(out_path)
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "").strip() or "Script failed."
            return False, report or None, err
        return True, report, None
    except subprocess.TimeoutExpired:
        if os.path.isfile(out_path):
            os.unlink(out_path)
        return False, "", "Request timed out (max 5 minutes)."
    except Exception as e:
        if os.path.isfile(out_path):
            try:
                os.unlink(out_path)
            except Exception:
                pass
        return False, "", str(e)


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/services")
def api_services():
    """Return list of services from services.sample.json for the dropdown."""
    return jsonify(load_services())


@app.route("/api/run", methods=["POST"])
def api_run():
    data = request.get_json() or {}
    env_vars = data.get("env", {})
    options = data.get("options", {})
    success, report, error = run_dashboard_check(env_vars, options)
    if error:
        return jsonify({"success": False, "error": error, "report": report or None}), 200
    return jsonify({"success": True, "report": report}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
