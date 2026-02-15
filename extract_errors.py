import os
import argparse
import requests
import json
from datetime import datetime, timezone, timedelta
from urllib.parse import quote

IST = timezone(timedelta(hours=5, minutes=30))

# --- AUTHENTICATION (override via env: OBSERVE_CUSTOMER_ID, OBSERVE_API_KEY, OBSERVE_CLUSTER) ---
CUSTOMER_ID = os.environ.get("OBSERVE_CUSTOMER_ID")
API_KEY = os.environ.get("OBSERVE_API_KEY")
OBSERVE_CLUSTER = os.environ.get("OBSERVE_CLUSTER", "eu-1").strip()
BASE_URL = f"https://{CUSTOMER_ID}.{OBSERVE_CLUSTER}.observeinc.com/v1/meta/export/query" if OBSERVE_CLUSTER else f"https://{CUSTOMER_ID}.observeinc.com/v1/meta/export/query"
# Defaults (override via env OBSERVE_WORKSPACE_ID / OBSERVE_DATASET_ID or CLI --workspace / --dataset)
DEFAULT_WORKSPACE_ID = os.environ.get("OBSERVE_WORKSPACE_ID", "41096433")
DEFAULT_DATASET_ID = os.environ.get("OBSERVE_DATASET_ID", "41250854")

# All regions (used with --all-regions). REGION env / --region filters OPAL by label(^Cluster).
REGIONS = ["aws-na", "aws-eu", "aws-au", "azure-na", "azure-eu", "gcp-na", "gcp-eu"]

# Default filenames (config for --all-services, output report)
DEFAULT_SERVICES_CONFIG = "services.sample.json"
OUTPUT_REPORT_FILE = "error_report.txt"

# Default OPAL pipeline (log JSON with level, message, context). Custom pipelines must output same columns:
# latest_timestamp, total_occurrences, error_msg, context (so the table and links work).
# {{REGION}} is replaced at runtime from env REGION (default "aws-na").
DEFAULT_OPAL_PIPELINE = """
make_col level:string(parse_json(string(log)).level)
| filter level = "error" or level = "ERROR"
| filter label(^Cluster) = "{{REGION}}"
| make_col error_msg:string(parse_json(string(log)).message), context:string(parse_json(string(log)).context)
| statsby group_by(error_msg, context), latest_timestamp: max(timestamp), total_occurrences: count()
| sort desc(latest_timestamp)
"""


def observe_ts_to_ist(ts):
    """Convert Observe timestamp (nanoseconds since epoch) to IST string."""
    if ts is None or ts == "N/A":
        return "N/A"
    try:
        sec = int(ts) / 1_000_000_000
        dt = datetime.fromtimestamp(sec, tz=timezone.utc).astimezone(IST)
        return dt.strftime("%Y-%m-%d %H:%M:%S IST")
    except (ValueError, TypeError, OSError):
        return str(ts)


def observe_ts_to_utc_datetime(ts):
    """Convert Observe timestamp (nanoseconds) to UTC datetime."""
    if ts is None:
        return None
    try:
        sec = int(ts) / 1_000_000_000
        return datetime.fromtimestamp(sec, tz=timezone.utc)
    except (ValueError, TypeError, OSError):
        return None


def build_observe_log_link(latest_ts_ns, error_msg, dataset_id, workspace_id):
    """Build a deep link to Observe log explorer for this error (dataset + time window around the error)."""
    host = f"{CUSTOMER_ID}.{OBSERVE_CLUSTER}.observeinc.com" if OBSERVE_CLUSTER else f"{CUSTOMER_ID}.observeinc.com"
    log_explorer_base = f"https://{host}/workspace/{workspace_id}/log-explorer"
    params = ["datasetId=" + quote(dataset_id)]
    dt = observe_ts_to_utc_datetime(latest_ts_ns)
    if dt:
        start = dt - timedelta(hours=1)
        end = min(dt + timedelta(minutes=15), datetime.now(timezone.utc))
        params.append("time-start=" + quote(start.strftime("%Y-%m-%dT%H:%M:%SZ")))
        params.append("time-end=" + quote(end.strftime("%Y-%m-%dT%H:%M:%SZ")))
    else:
        params.append("time-preset=PAST_24_HOURS")
    return log_explorer_base + "?" + "&".join(params)


def format_table(rows, headers, msg_max_width=400, link_width=90):
    """Format rows as a bordered table. Columns: IST, Count, Error (dynamic width), Link (fixed)."""
    if not rows:
        return ""
    n = len(headers)
    # Message column is second-to-last if we have Link, else last
    msg_col = -2 if n == 4 else -1
    max_msg = max((len(str(row[msg_col])) for row in rows), default=0)
    w_msg = min(max(max_msg, 40), msg_max_width)
    if n == 4:
        widths = [24, 8, w_msg, min(link_width, max((len(str(row[-1])) for row in rows), default=link_width))]
    else:
        widths = [24, 8, w_msg][:n]
    sep = "+" + "+".join("-" * (w + 2) for w in widths) + "+"
    lines = [sep]
    lines.append("| " + " | ".join(str(h)[:w].ljust(w) for h, w in zip(headers, widths)) + " |")
    lines.append(sep)
    for row in rows:
        cells = []
        for i, cell in enumerate(row):
            s = str(cell) if cell is not None else ""
            cells.append(s[: widths[i]].ljust(widths[i]) if i < len(widths) else s)
        lines.append("| " + " | ".join(cells) + " |")
    lines.append(sep)
    return "\n".join(lines)


def get_unique_errors(workspace_id=None, dataset_id=None, service_name=None, opal_pipeline=None, time_start_ist=None, time_end_ist=None, region_override=None):
    """Fetch unique errors for one service. Returns (table_str, success). Custom pipeline must output columns: latest_timestamp, total_occurrences, error_msg, context. time_start_ist/time_end_ist in IST (default: last 24h). region_override used for {{REGION}} in pipeline."""
    workspace_id = workspace_id or os.environ.get("OBSERVE_WORKSPACE_ID") or DEFAULT_WORKSPACE_ID
    dataset_id = dataset_id or os.environ.get("OBSERVE_DATASET_ID") or DEFAULT_DATASET_ID
    pipeline = apply_region_to_pipeline(opal_pipeline or DEFAULT_OPAL_PIPELINE, region_override).strip()
    label = service_name or "service"
    region = region_override if region_override is not None else os.environ.get("REGION", "aws-na")

    headers = {
        "Authorization": f"Bearer {CUSTOMER_ID} {API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/x-ndjson",
    }
    input_def = {"inputName": "main", "datasetId": dataset_id}
    payload = {
        "query": {
            "stages": [
                {
                    "input": [input_def],
                    "stageID": "errors",
                    "pipeline": pipeline,
                }
            ]
        },
        "rowCount": "10000",
    }
    time_params = build_query_time_params(time_start_ist, time_end_ist)
    url = f"{BASE_URL}?{time_params}"

    print(f"🚀 Extracting unique error signatures from {label} [region: {region}]...")
    try:
        response = requests.post(url, headers=headers, json=payload)
        if not response.ok:
            try:
                err_body = response.json()
                print(f"❌ API Error {response.status_code}: {err_body}")
            except Exception:
                print(f"❌ API Error {response.status_code}: {response.text[:500]}")
            return None, False
        text = response.text.strip()
        results = [json.loads(line) for line in text.split("\n")] if text else []

        if not results:
            print(f"No errors found for {label} [region: {region}].")
            return None, True

        table_headers = ("IST Timestamp", "Count", "Error & Context", "Link")
        table_rows = []
        for row in results:
            latest_ts = row.get("latest_timestamp")
            ts = observe_ts_to_ist(latest_ts)
            count = row.get("total_occurrences", 0)
            msg = row.get("error_msg", "No message")
            ctx = row.get("context", "No context")
            full_detail = f"[{ctx}] {msg}" if ctx and str(ctx) != "None" else (msg or "No message")
            link = build_observe_log_link(latest_ts, msg or full_detail, dataset_id, workspace_id)
            table_rows.append((ts, count, full_detail, link))
        table_str = "\n" + format_table(table_rows, table_headers, msg_max_width=800, link_width=220)
        return table_str, True
    except requests.exceptions.RequestException as e:
        print(f"❌ Connection Error: {e}")
        return None, False
    except Exception as e:
        print(f"❌ Error: {e}")
        return None, False

def load_pipeline(path):
    """Load OPAL pipeline from file. Returns None if path is empty or file not found."""
    if not path or not path.strip():
        return None
    path = path.strip()
    if os.path.isfile(path):
        with open(path, "r") as f:
            return f.read()
    print(f"⚠️ Pipeline file not found: {path}, using default pipeline.")
    return None


def apply_region_to_pipeline(pipeline, region_override=None):
    """Replace {{REGION}} in pipeline. Use region_override if set, else env REGION (default aws-na)."""
    if not pipeline:
        return pipeline
    region = region_override if region_override is not None else os.environ.get("REGION", "aws-na")
    return pipeline.replace("{{REGION}}", region)


def parse_ist_str(ist_str):
    """Parse IST time string to naive datetime (interpreted as IST). Accepts 'YYYY-MM-DD HH:MM:SS' or 'YYYY-MM-DDTHH:MM:SS'. Returns None on failure."""
    if not ist_str or not ist_str.strip():
        return None
    s = ist_str.strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s, fmt)
            # Treat as IST
            return dt.replace(tzinfo=IST)
        except ValueError:
            continue
    return None


def build_query_time_params(time_start_ist=None, time_end_ist=None):
    """Build Observe API time query params. If both start/end in IST provided, return time-start and time-end (UTC ISO); else return interval=24h."""
    if time_start_ist is None and time_end_ist is None:
        return "interval=24h"
    start_dt = parse_ist_str(time_start_ist) if time_start_ist else None
    end_dt = parse_ist_str(time_end_ist) if time_end_ist else None
    if start_dt is None or end_dt is None:
        return "interval=24h"
    start_utc = start_dt.astimezone(timezone.utc)
    end_utc = end_dt.astimezone(timezone.utc)
    return f"time-start={quote(start_utc.strftime('%Y-%m-%dT%H:%M:%SZ'))}&time-end={quote(end_utc.strftime('%Y-%m-%dT%H:%M:%SZ'))}"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Extract unique error signatures from Observe. Single service (default) or multiple via --config."
    )
    parser.add_argument("--workspace", "-w", type=str, default=None, help=f"Workspace ID (default: env or {DEFAULT_WORKSPACE_ID})")
    parser.add_argument("--dataset", "-d", type=str, default=None, help=f"Dataset ID (default: env or {DEFAULT_DATASET_ID})")
    parser.add_argument("--start", type=str, default=None, help="Start time in IST (YYYY-MM-DD HH:MM:SS). Default: last 24h. Env: START_IST")
    parser.add_argument("--end", type=str, default=None, help="End time in IST (YYYY-MM-DD HH:MM:SS). Default: last 24h. Env: END_IST")
    parser.add_argument("--pipeline-file", "-p", type=str, default=None, help="Path to OPAL pipeline file for this service (default pipeline used if omitted)")
    parser.add_argument("--config", "-c", type=str, default=None, help="JSON file defining multiple services; each may have pipeline_file or pipeline (see below)")
    parser.add_argument("--all-services", action="store_true", help=f"Run for all services (uses {DEFAULT_SERVICES_CONFIG} next to this script, or in cwd)")
    parser.add_argument("--all-regions", action="store_true", help="Run for all regions (aws-na, aws-eu, aws-au, azure-na, azure-eu, gcp-na, gcp-eu); combines with --config/--all-services or single-service")
    parser.add_argument("--auto", action="store_true", help="Same as --all-services --all-regions (all services × all regions)")
    parser.add_argument("--output", "-o", type=str, default=None, help="Output report file path (default: error_report.txt)")
    args = parser.parse_args()

    out_file = args.output or OUTPUT_REPORT_FILE

    if args.auto:
        args.all_services = True
        args.all_regions = True

    time_start = args.start or os.environ.get("START_IST")
    time_end = args.end or os.environ.get("END_IST")

    config_path = args.config
    if args.all_services and not config_path:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        for path in (os.path.join(script_dir, DEFAULT_SERVICES_CONFIG), DEFAULT_SERVICES_CONFIG):
            if os.path.isfile(path):
                config_path = path
                break
        if not config_path:
            print(f"❌ --all-services: {DEFAULT_SERVICES_CONFIG} not found (looked next to script and in cwd). Use --config <path>.")
            raise SystemExit(1)

    def run_services(region_override=None):
        """Run configured services (or single service), optionally for one region. Returns list of output sections."""
        out = []
        if config_path:
            with open(config_path, "r") as f:
                services = json.load(f)
            if not isinstance(services, list):
                services = [services]
            for svc in services:
                name = svc.get("name", svc.get("service_name", "Service"))
                w = svc.get("workspace_id") or args.workspace or os.environ.get("OBSERVE_WORKSPACE_ID") or DEFAULT_WORKSPACE_ID
                d = svc.get("dataset_id") or args.dataset or os.environ.get("OBSERVE_DATASET_ID") or DEFAULT_DATASET_ID
                pipeline = None
                if svc.get("pipeline_file"):
                    pipeline = load_pipeline(svc["pipeline_file"])
                elif svc.get("pipeline"):
                    pipeline = svc["pipeline"] if isinstance(svc["pipeline"], str) else None
                if pipeline is None:
                    pipeline = DEFAULT_OPAL_PIPELINE
                table_str, _ = get_unique_errors(workspace_id=w, dataset_id=d, service_name=name, opal_pipeline=pipeline, time_start_ist=time_start, time_end_ist=time_end, region_override=region_override)
                if table_str:
                    out.append(f"\n=== {name} ===\n{table_str}")
        else:
            pipeline = load_pipeline(args.pipeline_file) if args.pipeline_file else None
            table_str, _ = get_unique_errors(workspace_id=args.workspace, dataset_id=args.dataset, opal_pipeline=pipeline, time_start_ist=time_start, time_end_ist=time_end, region_override=region_override)
            if table_str:
                out.append(table_str)
        return out

    if args.all_regions:
        # Run for each region and aggregate output
        all_out = []
        for region in REGIONS:
            all_out.append(f"\n{'='*60}\n=== Region: {region} ===\n{'='*60}")
            sections = run_services(region_override=region)
            for s in sections:
                print(s)
                all_out.append(s)
        if all_out:
            with open(out_file, "w") as f:
                f.write("\n".join(all_out))
    else:
        all_out = run_services(region_override=None)
        for s in all_out:
            print(s)
        if all_out:
            with open(out_file, "w") as f:
                f.write("\n".join(all_out))
