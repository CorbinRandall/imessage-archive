FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

ENV DATA_DIR=/data
ENV QDRANT_URL=http://qdrant:6333
ENV COLLECTION=imessages
ENV EMBED_MODEL=BAAI/bge-small-en-v1.5

EXPOSE 8095

HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8095/health')" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8095"]
