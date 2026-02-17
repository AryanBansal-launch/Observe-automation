"""
Simple web app for Observe dashboard check.
Serves a frontend and runs extract_errors.py with user-provided env vars and options.
Uses async jobs + polling so long runs (e.g. all services × all regions) work within
platform request timeouts (e.g. Render's ~30s limit).
"""
import json
import os
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path

from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__, static_folder="static")
SCRIPT_DIR = Path(__file__).resolve().parent
SERVICES_CONFIG = SCRIPT_DIR / "services.sample.json"

# In-memory job store (use --workers 1 so one process owns it)
_jobs = {}
_jobs_lock = threading.Lock()
JOB_MAX_AGE_SEC = 3600  # drop jobs after 1 hour


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


def _run_job(job_id):
    """Background: run dashboard check and store result in _jobs."""
    job = _jobs.get(job_id)
    if not job or job.get("status") != "running":
        return
    env_vars = job.get("env_vars", {})
    options = job.get("options", {})
    success, report, error = run_dashboard_check(env_vars, options)
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id]["status"] = "done" if success else "failed"
            _jobs[job_id]["report"] = report
            _jobs[job_id]["error"] = error


def _cleanup_old_jobs():
    """Remove jobs older than JOB_MAX_AGE_SEC."""
    now = time.time()
    with _jobs_lock:
        for jid in list(_jobs):
            if now - _jobs[jid].get("created_at", 0) > JOB_MAX_AGE_SEC:
                del _jobs[jid]


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/services")
def api_services():
    """Return list of services from services.sample.json for the dropdown."""
    return jsonify(load_services())


@app.route("/api/run", methods=["POST"])
def api_run():
    try:
        data = request.get_json(silent=True) or {}
        env_vars = data.get("env", {})
        options = data.get("options", {})
        # Use async job so we return immediately and avoid platform request timeout (e.g. Render ~30s)
        job_id = str(uuid.uuid4())
        with _jobs_lock:
            _jobs[job_id] = {
                "status": "running",
                "report": None,
                "error": None,
                "env_vars": env_vars,
                "options": options,
                "created_at": time.time(),
            }
        thread = threading.Thread(target=_run_job, args=(job_id,))
        thread.daemon = True
        thread.start()
        return jsonify({"job_id": job_id}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e), "report": None}), 500


@app.route("/api/run/status/<job_id>")
def api_run_status(job_id):
    _cleanup_old_jobs()
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found or expired"}), 404
    status = job.get("status", "running")
    out = {"status": status}
    if status == "done":
        out["success"] = True
        out["report"] = job.get("report") or ""
    elif status == "failed":
        out["success"] = False
        out["error"] = job.get("error") or "Script failed."
        out["report"] = job.get("report")
    return jsonify(out), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
