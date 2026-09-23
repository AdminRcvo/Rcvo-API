FROM python:3.12-slim
WORKDIR /app
COPY src/ /app/src/
COPY profiles/ /app/profiles/
RUN useradd --create-home --uid 10001 rcvo && mkdir -p /data && chown -R rcvo:rcvo /data /app
USER rcvo
ENV PYTHONUNBUFFERED=1 PORT=8080
VOLUME ["/data"]
EXPOSE 8080
CMD ["python","/app/src/service.py","--state-db","/data/sourcing-agent.sqlite","--profiles-dir","/app/profiles"]
