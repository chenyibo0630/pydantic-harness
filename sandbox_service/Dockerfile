FROM python:3.12-slim

# tzdata so $TZ env var resolves to a real zoneinfo
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install skill script dependencies
COPY sandbox_service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy sandbox service + shared backend modules it depends on
COPY backend/core/sandbox/ backend/core/sandbox/
COPY backend/core/skills/ backend/core/skills/
COPY backend/core/__init__.py backend/core/__init__.py
COPY backend/__init__.py backend/__init__.py
COPY sandbox_service/ sandbox_service/

# Create non-root user and prep writable dirs before USER switch
RUN useradd -m -u 1000 -s /bin/bash sandboxuser \
    && mkdir -p /app/skills /app/workspace \
    && chown -R sandboxuser:sandboxuser /app

USER sandboxuser

ENV PYTHONPATH=/app
EXPOSE 8100

CMD ["uvicorn", "sandbox_service.app:app", "--host", "0.0.0.0", "--port", "8100"]
