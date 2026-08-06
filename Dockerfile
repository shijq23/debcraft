FROM python:3.13-slim

WORKDIR /app

# Install uv
RUN pip install --no-cache-dir uv

# Copy project files
COPY pyproject.toml uv.lock ./
COPY src/ src/
COPY tests/ tests/

# Install dependencies
RUN uv sync --locked

# Default command: run tests
CMD ["uv", "run", "pytest"]
