# ==============================================================================
# Stage 1: Builder
# Compiles wheels and packages dependencies to eliminate compilers and cached
# package wheels from the final runtime image.
# ==============================================================================
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /build

# Install build dependencies if needed and compile wheels
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip wheel --no-cache-dir --wheel-dir /build/wheels -r requirements.txt


# ==============================================================================
# Stage 2: Runtime
# Minimal, secure container runtime executing as a non-root appuser (UID 10001).
# ==============================================================================
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    HOST=0.0.0.0 \
    ENVIRONMENT=production

# Create non-root dedicated application user
RUN groupadd -g 10001 appuser && \
    useradd -u 10001 -g appuser -s /bin/false -M appuser

WORKDIR /app

# Install pre-built wheels from builder stage
COPY --from=builder /build/wheels /wheels
RUN pip install --no-cache-dir /wheels/* && rm -rf /wheels

# Copy application source code
COPY app /app/app

# Set safe ownership for non-root user
RUN chown -R appuser:appuser /app

# Run as non-root user
USER appuser

EXPOSE 8000

# Container orchestration healthcheck directive
HEALTHCHECK --interval=15s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz')" || exit 1

# Start FastAPI application with uvicorn
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
