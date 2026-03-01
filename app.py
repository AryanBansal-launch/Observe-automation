"""
Simple web app for Observe dashboard check.
Serves a frontend and runs extract_errors.py with user-provided env vars and options.
Uses async jobs + polling so long runs (e.g. all services × all regions) work within
platform request timeouts (e.g. Render's ~30s limit).
"""
import json
import logging
import os
import re
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import quote, urlparse

logger = logging.getLogger(__name__)

import requests
from flask import Flask, request, jsonify, send_from_directory

# Load .env so GEMINI_API_KEY, SLACK_WEBHOOK_URL, etc. are available when running locally
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).resolve().parent / ".env"
    load_dotenv(_env_path)
except ImportError:
    pass  # dotenv optional; use exported env or Render env vars

app = Flask(__name__, static_folder="static")
SCRIPT_DIR = Path(__file__).resolve().parent
SERVICES_CONFIG = SCRIPT_DIR / "services.sample.json"

# In-memory job store (use --workers 1 so one process owns it)
_jobs = {}
_jobs_lock = threading.Lock()
JOB_MAX_AGE_SEC = 3600  # drop jobs after 1 hour

# Hostname lookup cache: prefetched Nginx + Mgmt data (keyed by customer_id, cluster)
_lookup_cache = {}
_lookup_cache_lock = threading.Lock()
LOOKUP_CACHE_TTL_SEC = 600  # 10 min; use cache if younger than this
# HTTP timeout for Observe API calls (env OBSERVE_LOOKUP_TIMEOUT_SEC, default 300)
DEFAULT_LOOKUP_TIMEOUT_SEC = 300

# Free Gemini API (set GEMINI_API_KEY in env; get key at https://aistudio.google.com/app/apikey)
# GEMINI_MODEL: default gemini-1.5-flash; override with gemini-pro, gemini-1.5-pro, or gemini-2.0-flash if needed
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "").strip() or "gemini-1.5-flash"
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

# Optional default webhook URL for "Send me a Slack" (overridable by the URL input in the UI)
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "").strip()

SLACK_FORMAT_EXAMPLE = """AWS NA:

nginx-service (244)
Unexpected DNS Response

management-background-jobs-service:
Create notification failed. - @aryan.bansal is looking into this
[DeploymentControllerRMQ] error while deployment.onCreate on DeploymentControllerRMQ
[LoggingInterceptor] Event deployment.updateStatus Failed for deploymentUid: 6992ee12315fd334d17338b0

AWS EU:

management-bg-service:
[DeploymentControllerRMQ] error while deployment.onCreate on DeploymentControllerRMQ

Telemetry-service: (29)
Failed to send kafkaMessage

AZURE NA:

management-bg-jobs-service:
[Consumer] Crash: KafkaJSNumberOfRetriesExceeded
[TriggerK8sJobServices] Error while calling kubernetes API for triggerK8sJob. Link

Azure EU:

Telemetry-service:
otel service health check failed with status code: 503

GCP NA and GCP EU:

mgmt-bg-jobs-service:
Service unavailable exception

Logs-bg-jobs:
RMQ unavailable"""


# --- Hostname lookup: Nginx + Mgmt deployment queries (time range: OBSERVE_LOOKUP_DAYS or 15 mins) ---
NGINX_HOSTNAME_OPAL = r'''
filter label(^Namespace) = "contentfly"
make_col httpurl_detailshost:string(parse_json(string(log))["http.url_details.host"])
make_col orgUid:string(parse_json(string(log)).orgUid)
make_col projectUid:string(parse_json(string(log)).projectUid)
make_col environmentUid:string(parse_json(string(log)).environmentUid)
make_col cluster:string(label(^Cluster))
filter cluster = "aws-eu"
    or cluster = "aws-na"
    or cluster = "azure-na"
    or cluster = "azure-eu"
    or cluster = "gcp-na"
    or cluster = "gcp-eu"
    or cluster = "aws-au"
filter not is_null(httpurl_detailshost) and httpurl_detailshost != ""
    and not is_null(orgUid) and orgUid != ""
    and not is_null(projectUid) and projectUid != ""
    and not is_null(environmentUid) and environmentUid != ""
statsby orgUid:any(orgUid), projectUid:any(projectUid), environmentUid:any(environmentUid), cluster:any(cluster), group_by(httpurl_detailshost)
filter not is_null(orgUid) and orgUid != ""
    and not is_null(projectUid) and projectUid != ""
    and not is_null(environmentUid) and environmentUid != ""
    and not is_null(cluster) and cluster != ""
'''

MGMT_DEPLOYMENT_OPAL = r'''
filter label(^Namespace) = "contentfly"
make_col name:string(parse_json(string(log)).event.name)
filter name = "deployment.onCreate"
make_col deployment_uid:string(parse_json(string(log)).metadata.deployment_uid)
make_col success:bool(parse_json(string(log)).event.success)
make_col environmentUid:string(parse_json(string(log)).data.environment)
make_col projectUid:string(parse_json(string(log)).data.project)
make_col deploymentUrl:string(parse_json(string(log)).data.deploymentUrl)
make_col timestamp_1:string(parse_json(string(log)).timestamp)
filter success = true
filter not is_null(projectUid) and projectUid != ""
    and not is_null(environmentUid) and environmentUid != ""
    and not is_null(deployment_uid) and deployment_uid != ""
    and not is_null(deploymentUrl) and deploymentUrl != ""
    and not is_null(timestamp_1) and timestamp_1 != ""
statsby deployment_uid:last(deployment_uid), deploymentUrl:last(deploymentUrl), timestamp_1:last(timestamp_1), group_by(projectUid, environmentUid)
'''

NGINX_WORKSPACE = "41096433"
NGINX_DATASET = "41250854"
MGMT_BG_WORKSPACE = "41096433"
MGMT_BG_DATASET = "41249174"


def normalize_hostname(url_or_host):
    """Strip protocol, path, query, trailing slash; return hostname only (e.g. www.abc.com)."""
    if not url_or_host or not isinstance(url_or_host, str):
        return ""
    s = url_or_host.strip()
    if not s:
        return ""
    if "://" in s:
        try:
            parsed = urlparse(s if s.startswith("http") else "https://" + s)
            s = parsed.netloc or s
        except Exception:
            s = re.sub(r"^https?://", "", s, flags=re.IGNORECASE).split("/")[0]
    else:
        s = s.split("/")[0].split("?")[0]
    return s.rstrip("/").lower() or ""


def _observe_lookup_time_params():
    """
    Return query params for Observe export/query. API wants either (startTime + endTime) OR (interval) only.
    Set OBSERVE_LOOKUP_DAYS (e.g. 45) for past N days; if unset, use interval=15m.
    Returns dict suitable for requests: either {"startTime": ..., "endTime": ...} or {"interval": "15m"}.
    """
    lookup_days = os.environ.get("OBSERVE_LOOKUP_DAYS", "").strip()
    if lookup_days and lookup_days.isdigit() and int(lookup_days) > 0:
        now = datetime.now(timezone.utc)
        start = now - timedelta(days=int(lookup_days))
        return {"startTime": start.strftime("%Y-%m-%dT%H:%M:%SZ"), "endTime": now.strftime("%Y-%m-%dT%H:%M:%SZ")}
    return {"interval": "15m"}


def _fetch_lookup_data(customer_id, api_key, cluster):
    """
    Run both Nginx and Mgmt OPAL queries for the configured time range.
    Returns (nginx_rows, mgmt_rows, error_str). error_str is None on success.
    """
    logger.info("Lookup: fetching Nginx + Mgmt data from Observe (this may take a moment)...")
    time_params = _observe_lookup_time_params()
    nginx_rows, err = run_observe_opal_query(
        customer_id, api_key, cluster, NGINX_DATASET, NGINX_WORKSPACE,
        NGINX_HOSTNAME_OPAL, time_params,
    )
    if err:
        logger.warning("Lookup: Nginx query failed: %s", err)
        return [], [], err
    logger.info("Lookup: Nginx query returned %d rows", len(nginx_rows))
    mgmt_rows, err = run_observe_opal_query(
        customer_id, api_key, cluster, MGMT_BG_DATASET, MGMT_BG_WORKSPACE,
        MGMT_DEPLOYMENT_OPAL, time_params,
    )
    if err:
        logger.warning("Lookup: Mgmt deployment query failed: %s", err)
        return [], [], err
    logger.info("Lookup: Mgmt query returned %d rows; fetch complete", len(mgmt_rows))
    return nginx_rows, mgmt_rows, None


def _env_with_server_defaults(env_vars):
    """Merge request env with server env: Customer ID and API Key from request; cluster etc. from server if not in request."""
    env = dict(env_vars or {})
    if not _sanitize_observe_creds(env.get("OBSERVE_CLUSTER", "")):
        env["OBSERVE_CLUSTER"] = os.environ.get("OBSERVE_CLUSTER", "").strip()
    return env


def _sanitize_observe_creds(value):
    """Strip env value and remove newlines/quotes that can break Observe API auth."""
    if not value:
        return ""
    s = str(value).strip().replace("\r", "").replace("\n", "").strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        s = s[1:-1].strip()
    return s


def _prefetch_lookup_cache():
    """If OBSERVE_CUSTOMER_ID and OBSERVE_API_KEY are set in env, prefetch Nginx + Mgmt data into cache (background)."""

    def _run():
        try:
            from dotenv import load_dotenv
            load_dotenv(SCRIPT_DIR / ".env")
        except Exception:
            pass
        customer_id = _sanitize_observe_creds(os.environ.get("OBSERVE_CUSTOMER_ID", ""))
        api_key = _sanitize_observe_creds(os.environ.get("OBSERVE_API_KEY", ""))
        cluster = _sanitize_observe_creds(os.environ.get("OBSERVE_CLUSTER", ""))
        if not customer_id or not api_key:
            logger.debug("Lookup: prefetch skipped (no OBSERVE_CUSTOMER_ID or OBSERVE_API_KEY in env)")
            return
        base_url = f"https://{customer_id}.{cluster}.observeinc.com" if cluster else f"https://{customer_id}.observeinc.com"
        logger.info("Lookup: prefetching data on startup (customer_id=%s, cluster=%r, url=%s)...", customer_id, cluster or "(US default)", base_url)
        nginx_rows, mgmt_rows, err = _fetch_lookup_data(customer_id, api_key, cluster)
        with _lookup_cache_lock:
            if err:
                _lookup_cache[(customer_id, cluster)] = {"nginx_rows": [], "mgmt_rows": [], "error": err, "fetched_at": time.time()}
                logger.warning("Lookup: prefetch failed: %s", err)
            else:
                _lookup_cache[(customer_id, cluster)] = {"nginx_rows": nginx_rows, "mgmt_rows": mgmt_rows, "error": None, "fetched_at": time.time()}
                logger.info("Lookup: prefetch done (nginx=%d, mgmt=%d rows)", len(nginx_rows), len(mgmt_rows))

    t = threading.Thread(target=_run, daemon=True)
    t.start()


def run_observe_opal_query(customer_id, api_key, cluster, dataset_id, workspace_id, pipeline, time_params):
    """
    Run one OPAL query against Observe API. Returns (rows: list[dict], error: str|None).
    time_params: either {"startTime": iso, "endTime": iso} or {"interval": "15m"} (API accepts only one style).
    """
    customer_id = _sanitize_observe_creds(customer_id)
    api_key = _sanitize_observe_creds(api_key)
    cluster = _sanitize_observe_creds(cluster) if cluster else ""
    if ":" in api_key:
        logger.warning("Lookup: OBSERVE_API_KEY looks like a Datastream token (contains ':'). Export/query needs an API token from Observe Settings → My API tokens.")
    base = f"https://{customer_id}.{cluster}.observeinc.com" if cluster else f"https://{customer_id}.observeinc.com"
    url = f"{base}/v1/meta/export/query"
    params = dict(time_params)
    headers = {
        "Authorization": f"Bearer {customer_id} {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/x-ndjson",
    }
    payload = {
        "query": {
            "stages": [
                {
                    "input": [{"inputName": "main", "datasetId": dataset_id}],
                    "stageID": "q",
                    "pipeline": pipeline.strip(),
                }
            ]
        },
        "rowCount": "10000",
    }
    timeout_sec = DEFAULT_LOOKUP_TIMEOUT_SEC
    raw = os.environ.get("OBSERVE_LOOKUP_TIMEOUT_SEC", "").strip()
    if raw and raw.isdigit() and int(raw) > 0:
        timeout_sec = int(raw)
    try:
        logger.info("Lookup: running Observe query (dataset %s, timeout=%ss)...", dataset_id, timeout_sec)
        r = requests.post(url, params=params, headers=headers, json=payload, timeout=timeout_sec)
        if not r.ok:
            logger.warning("Lookup: Observe API error %s: %s", r.status_code, r.text[:200])
            err_msg = r.text[:500] if r.text else ""
            if r.status_code == 401:
                err_msg += " (Check: API token valid? Correct cluster? US tenant uses no OBSERVE_CLUSTER; EU uses OBSERVE_CLUSTER=eu-1)"
            return [], f"Observe API error {r.status_code}: {err_msg}"
        text = (r.text or "").strip()
        rows = [json.loads(line) for line in text.split("\n") if line.strip()]
        logger.info("Lookup: query returned %d rows", len(rows))
        return rows, None
    except requests.RequestException as e:
        return [], f"Request failed: {getattr(e, 'message', str(e))}"
    except json.JSONDecodeError as e:
        return [], f"Invalid response: {e}"


def hostname_lookup(hostname, env_vars):
    """
    Use cached (or freshly fetched) Nginx + Mgmt deployment data, filter by hostname and join.
    Creds (Customer ID, API Key) from request; cluster etc. from server env if not in request.
    Time range: OBSERVE_LOOKUP_DAYS (e.g. 45) = past N days; if unset, past 15 minutes.
    Returns (results: list[dict], error: str|None). Each result has: orgUid, projectUid, environmentUid,
    cluster (region), deployment_uid, deployment_timestamp, deploymentUrl.
    """
    env_vars = _env_with_server_defaults(env_vars)
    customer_id = _sanitize_observe_creds((env_vars or {}).get("OBSERVE_CUSTOMER_ID", ""))
    api_key = _sanitize_observe_creds((env_vars or {}).get("OBSERVE_API_KEY", ""))
    cluster = _sanitize_observe_creds((env_vars or {}).get("OBSERVE_CLUSTER", ""))
    if not customer_id or not api_key:
        return [], "OBSERVE_CUSTOMER_ID and OBSERVE_API_KEY are required."

    normalized = normalize_hostname(hostname)
    if not normalized:
        return [], "Please enter a valid hostname or URL."

    logger.info("Lookup: hostname search started for %r (normalized: %s)", hostname.strip(), normalized)

    cache_key = (customer_id, cluster)
    now_ts = time.time()

    # Use cache if valid (same key and not expired)
    with _lookup_cache_lock:
        entry = _lookup_cache.get(cache_key)
        if entry and (now_ts - entry.get("fetched_at", 0)) <= LOOKUP_CACHE_TTL_SEC and entry.get("error") is None:
            nginx_rows = entry.get("nginx_rows") or []
            mgmt_rows = entry.get("mgmt_rows") or []
        else:
            nginx_rows = mgmt_rows = None

    if nginx_rows is None or mgmt_rows is None:
        logger.info("Lookup: cache miss or expired; fetching fresh data from Observe...")
        nginx_rows, mgmt_rows, err = _fetch_lookup_data(customer_id, api_key, cluster)
        if err:
            return [], err
        with _lookup_cache_lock:
            _lookup_cache[cache_key] = {"nginx_rows": nginx_rows, "mgmt_rows": mgmt_rows, "error": None, "fetched_at": time.time()}
    else:
        logger.info("Lookup: using cached data (%d nginx, %d mgmt rows)", len(nginx_rows), len(mgmt_rows))

    # Build lookup by (projectUid, environmentUid)
    deployment_by_key = {}
    for row in mgmt_rows:
        pu = (row.get("projectUid") or "").strip()
        eu = (row.get("environmentUid") or "").strip()
        if pu and eu:
            deployment_by_key[(pu, eu)] = {
                "deployment_uid": (row.get("deployment_uid") or "").strip(),
                "deployment_timestamp": (row.get("timestamp_1") or "").strip(),
                "deploymentUrl": (row.get("deploymentUrl") or "").strip(),
            }

    # Filter Nginx by normalized hostname and join deployment info
    results = []
    for row in nginx_rows:
        h = (row.get("httpurl_detailshost") or "").strip().lower()
        if h != normalized:
            continue
        org = (row.get("orgUid") or "").strip()
        proj = (row.get("projectUid") or "").strip()
        env = (row.get("environmentUid") or "").strip()
        reg = (row.get("cluster") or "").strip()
        dep = deployment_by_key.get((proj, env)) or {}
        results.append({
            "orgUid": org,
            "projectUid": proj,
            "environmentUid": env,
            "region": reg,
            "deployment_uid": dep.get("deployment_uid", ""),
            "deployment_timestamp": dep.get("deployment_timestamp", ""),
            "deploymentUrl": dep.get("deploymentUrl", ""),
        })

    logger.info("Lookup: done for %s — %d result(s)", normalized, len(results))
    return results, None


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


@app.route("/api/hostname-lookup/prefetch", methods=["POST"])
def api_hostname_lookup_prefetch():
    """
    Prefetch Nginx + Mgmt data using Customer ID and API Key from the request. Cluster etc. from server env.
    Call this after the user has entered creds in the UI so lookups can use cached data.
    Body: { "env": { "OBSERVE_CUSTOMER_ID", "OBSERVE_API_KEY" } }
    """
    try:
        data = request.get_json(silent=True) or {}
        env_vars = data.get("env") or {}
        logger.info("Lookup: prefetch requested (customer_id from UI)")
        success, err = _run_prefetch_with_creds(env_vars)
        if not success:
            return jsonify({"success": False, "error": err or "Prefetch failed"}), 200
        return jsonify({"success": True, "message": "Data loaded; you can run lookups now."}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/hostname-lookup", methods=["POST"])
def api_hostname_lookup():
    """
    Look up orgUid, projectUid, environmentUid, region, latest deployment UID and timestamp by hostname.
    Uses Customer ID and API Key from request; cluster etc. from server env. Pre-fetches on cache miss.
    Body: { "hostname": "www.abc.com or https://www.abc.com/...", "env": { "OBSERVE_CUSTOMER_ID", "OBSERVE_API_KEY" } }
    """
    try:
        data = request.get_json(silent=True) or {}
        hostname = (data.get("hostname") or "").strip()
        env_vars = data.get("env") or {}
        logger.info("Lookup: API request for hostname %r", hostname or "(empty)")
        results, err = hostname_lookup(hostname, env_vars)
        if err:
            logger.warning("Lookup: failed for %r — %s", hostname, err)
            return jsonify({"success": False, "error": err, "results": [], "normalized_hostname": normalize_hostname(hostname)}), 200
        return jsonify({
            "success": True,
            "results": results,
            "normalized_hostname": normalize_hostname(hostname),
        }), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e), "results": []}), 500


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


def _build_slack_blocks(plain_text):
    """Convert formatted report text into Slack Block Kit (header, divider, mrkdwn sections)."""
    def escape(s):
        return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    lines = (plain_text or "").split("\n")
    region_pattern = re.compile(
        r"^(AWS NA|AWS EU|AZURE NA|Azure EU|GCP NA and GCP EU|GCP NA|GCP EU)\s*:?\s*$",
        re.IGNORECASE,
    )
    service_pattern = re.compile(r"^([a-zA-Z0-9][a-zA-Z0-9\-_\.]+(?:\s*\([0-9]+\))?)\s*:?\s*$")

    out_lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            out_lines.append("")
            continue
        if region_pattern.match(stripped):
            out_lines.append("*" + escape(stripped.rstrip(":")) + ":*")
        elif service_pattern.match(stripped) and (" " in stripped or ":" in stripped):
            out_lines.append("*" + escape(stripped.rstrip(":")) + "*")
        else:
            out_lines.append(escape(line))
    mrkdwn_body = "\n".join(out_lines)

    block_max = 2900
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "Dashboard error report", "emoji": True}},
        {"type": "divider"},
    ]
    if len(mrkdwn_body) <= block_max:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": mrkdwn_body}})
    else:
        pos = 0
        while pos < len(mrkdwn_body):
            end = min(pos + block_max, len(mrkdwn_body))
            if end < len(mrkdwn_body):
                last_nl = mrkdwn_body.rfind("\n", pos, end + 1)
                if last_nl > pos:
                    end = last_nl + 1
            chunk = mrkdwn_body[pos:end].strip()
            if chunk:
                blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": chunk}})
            pos = end
    return {"text": plain_text[:4000], "blocks": blocks}


def _format_report_as_slack_message(report_text):
    """Use Gemini to format the dashboard report as a Slack-style message. Returns (message, error)."""
    if not GEMINI_API_KEY:
        return None, "GEMINI_API_KEY is not set. Add it in env (get a free key at https://aistudio.google.com/app/apikey)."
    if not report_text or not report_text.strip():
        return None, "No report content to format."
    prompt = f"""You are formatting an error dashboard report for a Slack message. Convert the following report into a clean Slack message.

Rules:
- Group by region first (e.g. AWS NA:, AWS EU:, AZURE NA:, Azure EU:, GCP NA and GCP EU:). Use these exact region headers when the report contains data for those regions.
- Under each region, list service names. Optionally add error count in parentheses after the name if present, e.g. "nginx-service (244)".
- Under each service, list the error messages (one per line, indented or with a newline). Keep the original error text; do not invent or add @mentions.
- Use blank lines between regions and between services for readability.
- Output only the formatted message, no preamble or explanation.

Example format:
{SLACK_FORMAT_EXAMPLE}

Report to format:
---
{report_text[:30000]}
---
"""
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 8192},
    }
    url = f"{GEMINI_URL}?key={GEMINI_API_KEY}"
    headers = {"Content-Type": "application/json"}
    max_retries = 3
    retry_delay = 8  # seconds; free tier is often 1 req/min or similar
    last_error = None
    for attempt in range(max_retries):
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=60)
            if resp.status_code == 429:
                last_error = "Rate limit exceeded (429). Wait a minute and try again, or check Gemini free tier limits."
                if attempt < max_retries - 1:
                    time.sleep(retry_delay * (attempt + 1))
                    continue
                return None, last_error
            resp.raise_for_status()
            data = resp.json()
            parts = (data.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
            if not parts:
                return None, "Gemini returned no text."
            text = (parts[0].get("text") or "").strip()
            if not text:
                return None, "Gemini returned empty text."
            return text, None
        except requests.RequestException as e:
            last_error = getattr(e, "message", None) or str(e)
            if hasattr(e, "response") and e.response is not None and e.response.status_code == 429:
                if attempt < max_retries - 1:
                    time.sleep(retry_delay * (attempt + 1))
                    continue
                return None, "Rate limit exceeded (429). Wait a minute and try again."
            return None, f"Gemini API request failed: {last_error}"
        except (KeyError, IndexError, TypeError) as e:
            return None, f"Unexpected Gemini response: {e}"
    return None, last_error or "Gemini API request failed."


@app.route("/api/slack-message", methods=["POST"])
def api_slack_message():
    """Format the last report as a Slack message using Gemini, and optionally POST it to a webhook URL."""
    try:
        data = request.get_json(silent=True) or {}
        report_text = (data.get("report_text") or "").strip()
        webhook_url = (data.get("webhook_url") or "").strip() or SLACK_WEBHOOK_URL

        slack_message, err = _format_report_as_slack_message(report_text)
        if err:
            return jsonify({"success": False, "error": err, "slack_message": None}), 200

        # Build proper Slack payload: Block Kit (header, divider, mrkdwn sections) + plain text fallback
        payload = _build_slack_blocks(slack_message)
        sent_to_url = None
        if webhook_url:
            try:
                r = requests.post(
                    webhook_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=15,
                )
                if not r.ok:
                    return jsonify({
                        "success": False,
                        "error": f"Webhook returned {r.status_code}: {r.text[:200]}",
                        "slack_message": slack_message,
                        "sent_to_url": False,
                    }), 200
                sent_to_url = True
            except requests.RequestException as e:
                return jsonify({
                    "success": True,
                    "slack_message": slack_message,
                    "sent_to_url": False,
                    "error": f"Could not send to URL: {getattr(e, 'message', str(e))}",
                }), 200

        return jsonify({
            "success": True,
            "slack_message": slack_message,
            "sent_to_url": sent_to_url,
            "payload": payload,
        }), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e), "slack_message": None}), 500


# No startup prefetch; prefetch runs only when user triggers it via UI (Load data button) or on first Look up.


def _run_prefetch_with_creds(env_vars):
    """Run Nginx + Mgmt fetch with given creds (env_vars merged with server env), store in cache. Returns (success, error_msg)."""
    env_vars = _env_with_server_defaults(env_vars)
    customer_id = _sanitize_observe_creds((env_vars or {}).get("OBSERVE_CUSTOMER_ID", ""))
    api_key = _sanitize_observe_creds((env_vars or {}).get("OBSERVE_API_KEY", ""))
    cluster = _sanitize_observe_creds((env_vars or {}).get("OBSERVE_CLUSTER", ""))
    if not customer_id or not api_key:
        return False, "OBSERVE_CUSTOMER_ID and OBSERVE_API_KEY are required."
    nginx_rows, mgmt_rows, err = _fetch_lookup_data(customer_id, api_key, cluster)
    if err:
        return False, err
    with _lookup_cache_lock:
        _lookup_cache[(customer_id, cluster)] = {"nginx_rows": nginx_rows, "mgmt_rows": mgmt_rows, "error": None, "fetched_at": time.time()}
    return True, None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
