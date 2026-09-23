FROM python:3.12-slim
WORKDIR /app
COPY requirements-ingestion.txt /app/requirements-ingestion.txt
RUN python -m pip install --no-cache-dir -r /app/requirements-ingestion.txt
COPY schema/ /app/schema/
COPY src/ /app/src/
COPY scripts/ /app/scripts/
COPY connectors/ /app/connectors/
RUN useradd --create-home --uid 10001 rcvo && mkdir -p /data && chown -R rcvo:rcvo /data /app
USER rcvo
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app/src PORT=8080
VOLUME ["/data"]
EXPOSE 8080
CMD ["python","/app/scripts/sourcing_api.py","--data-dir","/data","--bind","0.0.0.0","--port","8080"]
