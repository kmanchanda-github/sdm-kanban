FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY templates/ templates/
COPY static/ static/

RUN mkdir -p /app/data

ENV PYTHONUNBUFFERED=1
ENV PORT=9999
ENV DATA_DIR=/app/data

EXPOSE 9999

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:9999/api/health')"

CMD ["gunicorn", "--bind", "0.0.0.0:9999", "--workers", "2", "--threads", "4", "--timeout", "120", "app:app"]
