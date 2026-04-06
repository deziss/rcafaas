FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --default-timeout=100 --no-cache-dir -r requirements.txt

# Install PyRCA without dependencies to avoid javabridge build failure
RUN pip install --no-deps sfr-pyrca==1.0.1

# Create non-root user (needs write access to /tmp for health file)
RUN groupadd -r appuser && useradd -r -g appuser appuser

COPY . .

USER appuser

CMD ["python", "worker.py"]
