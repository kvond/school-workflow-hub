FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY preflight ./preflight

# Cloud Run mounts config.yaml from Secret Manager at /config/config.yaml.
ENV PREFLIGHT_CONFIG=/config/config.yaml
ENTRYPOINT ["python", "-m", "preflight.cli"]
CMD ["run", "--publish"]
