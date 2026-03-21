# Observe – web app (Flask + gunicorn)
FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app (Flask, scripts, config, pipelines); .dockerignore excludes .env and output/
COPY . .

ENV PORT=5000
EXPOSE 5000
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT} --workers 1 --timeout 330 app:app"]
