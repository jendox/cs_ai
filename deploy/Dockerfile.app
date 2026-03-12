FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

# Copy full project (run.py imports local src package directly).
COPY . /app

# Install runtime dependencies and the project itself.
RUN uv sync --frozen --no-dev

CMD ["uv", "run", "python", "run.py"]
