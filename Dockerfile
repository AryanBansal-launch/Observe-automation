# Observe error extraction – run without installing Python locally
FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app (script, config, pipelines); .dockerignore excludes .env and cache
COPY . .

# Pass env at runtime (--env-file .env or -e). Override args: docker run ... --auto
ENTRYPOINT ["python3", "extract_errors.py"]
CMD ["--all-services"]
