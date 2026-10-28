FROM python:3.13-slim

# Tesseract enables the real OCR path for scanned pages; libglib is needed by OpenCV (QR + image checks).
RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng libglib2.0-0 curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini pytest.ini ./
COPY tests ./tests

RUN useradd --create-home --uid 10001 appuser && mkdir -p /app/data && chown -R appuser /app
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s CMD curl -fs http://localhost:8000/api/v1/health || exit 1
# Migrations and (demo-only) seeding run in the application lifespan.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
