FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (cache-friendly)
COPY pyproject.toml .
RUN pip install --no-cache-dir --timeout=60 --retries=3 .

# Copy application code
COPY backend/ backend/
COPY main_agent/ main_agent/
COPY skills/ skills/

# PYTHONPATH so backend/ is importable without sys.path hack
ENV PYTHONPATH=/app

# Workspace volume mount point
RUN mkdir -p /app/workspace
VOLUME ["/app/workspace"]

EXPOSE 2648

CMD ["uvicorn", "main_agent.server:app", "--host", "0.0.0.0", "--port", "2648"]
