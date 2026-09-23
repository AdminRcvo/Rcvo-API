FROM python:3.12-slim
WORKDIR /app
COPY schema/ /app/schema/
COPY src/ /app/src/
COPY scripts/ /app/scripts/
COPY examples/ /app/examples/
RUN useradd --create-home --uid 10001 rcvo && mkdir -p /data && chown -R rcvo:rcvo /data /app
USER rcvo
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app/src PORT=8080
VOLUME ["/data"]
EXPOSE 8080
CMD ["python","/app/scripts/sourcing_api.py","--db","/data/rcvo-reference.sqlite","--bind","0.0.0.0","--port","8080"]
