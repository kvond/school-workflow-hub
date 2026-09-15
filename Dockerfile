FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/src PORT=8080
WORKDIR /app
COPY requirements-hosting.txt ./
RUN python -m pip install --no-cache-dir --require-hashes -r requirements-hosting.txt \
    && groupadd --gid 10001 hub \
    && useradd --uid 10001 --gid hub --no-create-home --shell /usr/sbin/nologin hub
COPY gunicorn.conf.py ./
COPY src/ ./src/
COPY web/ ./web/
USER 10001:10001
EXPOSE 8080
CMD ["gunicorn", "--config", "gunicorn.conf.py", "school_workflow_hub.web:application"]
