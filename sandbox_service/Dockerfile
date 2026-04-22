FROM python:3.12-slim

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

# Skills are mounted as volume at runtime
RUN mkdir -p /app/skills /app/workspace

ENV PYTHONPATH=/app
EXPOSE 8100

CMD ["uvicorn", "sandbox_service.app:app", "--host", "0.0.0.0", "--port", "8100"]
