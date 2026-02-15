# Observe – Error extraction from log datasets

This folder contains a script and config to extract **unique error signatures** from Observe log datasets across multiple services and regions. Output is printed to the terminal and written to `error_report.txt` (IST timestamps, counts, error messages, and deep links to the Observe log explorer).

**New here?** → See [Setup (step-by-step)](#setup-step-by-step) to run locally, or [Running with Docker](#running-with-docker) to run in a container.

---

## What’s included

| Item | Description |
|------|-------------|
| **extract_errors.py** | Main script: calls Observe API, runs OPAL pipelines, prints/writes results. |
| **env.sample** | Sample environment variables. Copy to `.env` and set values (do not commit real keys). |
| **services.sample.json** | List of services (name, workspace_id, dataset_id, pipeline_file) for multi-service runs. |
| **pipelines/** | OPAL pipeline files (one per service or shared). Use `{{REGION}}` for cluster filter; script replaces it at runtime. |
| **error_report.txt** | Written on each run: table(s) of unique errors (and links). |

### Services in `services.sample.json`

- Launch Management  
- Launch Management Background Jobs Service  
- Launch Logs service  
- Launch Logs bg service  
- Launch telemetry service  
- Launch logs-bg-exporter-service  
- Launch Nginx service  
- Launch Deployment Agent  

Each entry can override `workspace_id`, `dataset_id`, and `pipeline_file`. Pipeline files live under `pipelines/` and must output columns: `latest_timestamp`, `total_occurrences`, `error_msg`, `context`.

---

## How to get your Customer ID and API token

### Customer ID (OBSERVE_CUSTOMER_ID)

1. Open your Observe workspace in the browser, e.g.:  
   **https://143110822295.eu-1.observeinc.com/workspace/41096433/home?tab=Favorites**
2. The **Customer ID** is the first segment after `https://` — i.e. the subdomain before `.observeinc.com`.  
   - From `https://143110822295.eu-1.observeinc.com/workspace/...` → Customer ID is **`143110822295`**.

Set this in `.env` as **OBSERVE_CUSTOMER_ID**.

### API token (OBSERVE_API_KEY)

1. Go to the API tokens page in your Observe instance:  
   **https://143110822295.eu-1.observeinc.com/settings/my-api-tokens**  
   (Replace `143110822295` and `eu-1` with your own customer ID and cluster if different.)
2. Create a new API token (or use an existing one). Copy the token value once; it may not be shown again.
3. Set it in your environment or `.env` as **OBSERVE_API_KEY** (see [Environment variables](#environment-variables)).

The script sends: `Authorization: Bearer <OBSERVE_CUSTOMER_ID> <OBSERVE_API_KEY>`.  
Ensure the token’s user has **dataset:view** (or equivalent) on the datasets you query.

---

## Setup (step-by-step)

Follow this flow to get running from scratch.

### 1. Prerequisites

- **Python 3** (3.8+ recommended)
- Access to your Observe instance (Customer ID and API token)

### 2. Get the code

Clone or open the repo and go to the **project root** (the folder that contains the `Observe` directory):

```bash
cd /path/to/Observe-automation
```

### 3. Install dependencies

From the project root (where `requirements.txt` lives):

```bash
pip install -r requirements.txt
```

Or from inside `Observe/`:

```bash
cd Observe
pip install -r ../requirements.txt
```

*(Optional)* Use a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate   # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 4. Configure environment

1. Go into the `Observe` folder (if not already there):
   ```bash
   cd Observe
   ```
2. Copy the sample env file and edit it with your values:
   ```bash
   cp env.sample .env
   ```
3. In `.env`, set at least:
   - **OBSERVE_CUSTOMER_ID** – from your Observe URL (see [How to get your Customer ID and API token](#how-to-get-your-customer-id-and-api-token))
   - **OBSERVE_API_KEY** – from Observe → Settings → My API tokens  
   Optionally set **OBSERVE_CLUSTER**, **OBSERVE_WORKSPACE_ID**, **OBSERVE_DATASET_ID**, and **REGION** as needed.

**Do not commit `.env`** (it contains secrets).

### 5. Load env and run

From the **Observe** folder, load your env and run the script:

```bash
# Load environment variables (choose one)
export $(grep -v '^#' .env | xargs)
# Or: set -a && source .env && set +a

# Single service (default workspace/dataset from .env)
python3 extract_errors.py

# All services from services.sample.json
python3 extract_errors.py --all-services

# All services × all regions (full report)
python3 extract_errors.py --auto
```

Results are printed to the terminal and written to **error_report.txt** in the same folder.

### 6. (Optional) Run the web UI

A simple frontend lets you set env vars and run the same checks from the browser:

```bash
cd Observe
pip install -r requirements.txt   # includes Flask
python3 app.py
```

Open **http://localhost:5000**. Enter **OBSERVE_CUSTOMER_ID** and **OBSERVE_API_KEY** (required), optionally expand and set cluster, workspace, dataset, region, and time range. Choose a run mode (Single service, All services, All regions, or Auto) and click **Run dashboard check**. The report appears on the page; you can copy or download it.

---

## Quick reference

| Step | What to do |
|------|------------|
| Install | `pip install -r requirements.txt` (from project root) |
| Config | `cd Observe` → `cp env.sample .env` → set **OBSERVE_CUSTOMER_ID** and **OBSERVE_API_KEY** |
| Run | Load `.env`, then `python3 extract_errors.py --all-services` (or `--auto`) |
| Output | Terminal + `Observe/error_report.txt` |
| **Web UI** | `cd Observe && python3 app.py` → open http://localhost:5000 ([details](#6-optional-run-the-web-ui)) |
| **Deploy on Render** | Push to GitHub → connect repo at [Render](https://render.com) → deploy ([details](#deploy-on-render)) |
| **Docker** | `docker build -t observe-extract-errors Observe` then `docker run --rm --env-file .env observe-extract-errors` ([details](#running-with-docker)) |

---

## Deploy on Render

You can host the web UI on [Render](https://render.com) for free (with limits).

1. **Push your code** to a GitHub (or GitLab) repository. Ensure the repo root contains the `Observe` folder and the root `render.yaml`.

2. **Create a Web Service** on Render:
   - Go to [dashboard.render.com](https://dashboard.render.com) → **New** → **Web Service**.
   - Connect your repository.
   - If you use the repo’s **Blueprint** (`render.yaml`), Render will create the service from it. Otherwise set:
     - **Root Directory:** `Observe`
     - **Runtime:** Python 3
     - **Build Command:** `pip install -r requirements.txt`
     - **Start Command:** `gunicorn --bind 0.0.0.0:$PORT app:app`

3. **Deploy.** Render will build and run the app. Your URL will be like `https://observe-dashboard-check.onrender.com`.

4. **Credentials:** The app does not store Observe credentials on the server. Users enter **OBSERVE_CUSTOMER_ID** and **OBSERVE_API_KEY** in the browser (and can save them in localStorage).

**Note:** On the free tier, requests may time out after ~30–60 seconds. For long “Run dashboard check” runs (e.g. All services × All regions), use a single service or fewer regions, or consider a paid plan for longer timeouts.

---

## Running with Docker

You can run the tool in a container so you don’t need Python installed locally.

### Build the image

From the **Observe** folder (or project root with `-f`):

```bash
cd Observe
docker build -t observe-extract-errors .
```

Or from project root:

```bash
docker build -t observe-extract-errors -f Observe/Dockerfile Observe
```

### Run with env file

Create `.env` in `Observe/` (from `env.sample`) with your credentials, then:

```bash
cd Observe
docker run --rm --env-file .env observe-extract-errors
```

Default command is `--all-services`. Override with any flags:

```bash
docker run --rm --env-file .env observe-extract-errors --auto
docker run --rm --env-file .env observe-extract-errors -d 41250854 -p pipelines/launch_nginx_errors.opal
```

### Run with inline env (no .env file)

```bash
docker run --rm \
  -e OBSERVE_CUSTOMER_ID=your_customer_id \
  -e OBSERVE_API_KEY=your_api_key \
  -e OBSERVE_CLUSTER=eu-1 \
  observe-extract-errors --all-services
```

### Save the report to your host

The script writes `error_report.txt` inside the container. Mount a directory to get it on your machine:

```bash
docker run --rm --env-file .env -v "$(pwd)/output:/app" observe-extract-errors --auto
```

Then open `./output/error_report.txt`.

---

## Environment variables

Copy `env.sample` to `.env` in this folder (or export in the shell). Load before running, e.g.:

```bash
set -a && source .env && set +a && python3 extract_errors.py --all-services
# or
export $(grep -v '^#' .env | xargs) && python3 extract_errors.py --all-services
```

| Variable | Purpose | Default (if any) |
|----------|---------|-------------------|
| **OBSERVE_CUSTOMER_ID** | Your Observe customer ID (in the URL). | `143110822295` |
| **OBSERVE_API_KEY** | API token for authentication. | *(none – set this)* |
| **OBSERVE_CLUSTER** | Regional cluster (e.g. `eu-1`). Base URL: `https://<customer>.<cluster>.observeinc.com/...` | `eu-1` |
| **OBSERVE_WORKSPACE_ID** | Default workspace for single-service or fallback in config. | `41096433` |
| **OBSERVE_DATASET_ID** | Default dataset when running a single service. | (e.g. `41249174`) |
| **REGION** | Value for `{{REGION}}` in OPAL (cluster filter, e.g. `label(^Cluster) = "{{REGION}}"`). | `aws-na` |
| **START_IST** | Start of time window in IST (`YYYY-MM-DD HH:MM:SS`). Optional; with **END_IST** overrides “last 24h”. | — |
| **END_IST** | End of time window in IST. Optional. | — |

**Valid regions (for REGION or --all-regions):**  
`aws-na`, `aws-eu`, `aws-au`, `azure-na`, `azure-eu`, `gcp-na`, `gcp-eu`

---

## Commands and flags

Run from the **Observe** folder (or pass correct paths to `--config` / `--pipeline-file`).

### Single service (default workspace/dataset from env)

```bash
python3 extract_errors.py
```

Uses `OBSERVE_WORKSPACE_ID`, `OBSERVE_DATASET_ID`, `REGION`, and last 24 hours.

### Single service with explicit dataset and pipeline

```bash
python3 extract_errors.py -d <dataset_id> -p pipelines/<pipeline>.opal
```

Example (Nginx only):

```bash
python3 extract_errors.py -d 41250854 -p pipelines/launch_nginx_errors.opal
```

### All services (from `services.sample.json`)

```bash
python3 extract_errors.py --all-services
```

Finds `services.sample.json` next to `extract_errors.py` (or in cwd) and runs every service in it.

### Custom services config

```bash
python3 extract_errors.py --config path/to/services.json
```

### All regions (one region at a time, with section headers)

Runs the same run (all services or single) for each region and concatenates output:

```bash
python3 extract_errors.py --all-services --all-regions
```

Or with a custom config:

```bash
python3 extract_errors.py --config services.json --all-regions
```

### Auto (all services × all regions)

Equivalent to `--all-services --all-regions`:

```bash
python3 extract_errors.py --auto
```

### Time window (IST)

Override default “last 24 hours” by setting both start and end in IST:

```bash
python3 extract_errors.py --all-services --start "2026-02-13 00:00:00" --end "2026-02-14 12:00:00"
```

Or use env: `START_IST` and `END_IST`.

### Override workspace/dataset from CLI

```bash
python3 extract_errors.py -w <workspace_id> -d <dataset_id>
```

---

## Flag reference

| Flag | Short | Description |
|------|--------|-------------|
| **--workspace** | **-w** | Override workspace ID. |
| **--dataset** | **-d** | Override dataset ID (single-service). |
| **--start** | — | Start time in IST (`YYYY-MM-DD HH:MM:SS`). Env: `START_IST`. |
| **--end** | — | End time in IST. Env: `END_IST`. |
| **--pipeline-file** | **-p** | Path to OPAL pipeline file (single-service). |
| **--config** | **-c** | Path to JSON file listing services (name, workspace_id, dataset_id, pipeline_file). |
| **--all-services** | — | Use `services.sample.json` in this folder (or cwd) as config. |
| **--all-regions** | — | Run for every region (`aws-na`, `aws-eu`, …) and print/write combined output. |
| **--auto** | — | Same as `--all-services --all-regions` (all services × all regions). |

---

## Output

- **Terminal:** Progress lines like `🚀 Extracting unique error signatures from <service> [region: <region>]...` and one table per service (and per region when using `--all-regions`).  
- **error_report.txt:** Same table(s) in one file (IST timestamp, count, error & context, link to Observe log explorer).

---

## Pipelines and `{{REGION}}`

- Pipeline files under **pipelines/** define the OPAL (filters, `make_col`, `statsby`, etc.).  
- The script replaces **`{{REGION}}`** in the pipeline with the current region (env **REGION** or the loop value when using **--all-regions**).  
- Custom pipelines must output: **latest_timestamp**, **total_occurrences**, **error_msg**, **context** so the script can build the table and links.  
- To copy OPAL from the Observe UI: Worksheet → query editor → **OPAL** tab; or Log Explorer → query builder → **OPAL**. Use only the pipeline part (no `interface "..."` line).

---

## Quick start

For full steps see [Setup (step-by-step)](#setup-step-by-step). Short version:

1. From project root: `pip install -r requirements.txt`
2. `cd Observe` → copy `env.sample` to `.env` and set **OBSERVE_CUSTOMER_ID** and **OBSERVE_API_KEY**
3. Load env: `export $(grep -v '^#' .env | xargs)` (or `set -a && source .env && set +a`)
4. Run: `python3 extract_errors.py --all-services` or `python3 extract_errors.py --auto`
5. Check **error_report.txt** for the full report
