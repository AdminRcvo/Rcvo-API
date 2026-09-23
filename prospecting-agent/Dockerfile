FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir -r /app/requirements.txt
COPY src/ /app/src/
COPY config/ /app/config/
RUN useradd --create-home --uid 10001 rcvo && mkdir -p /data && chown -R rcvo:rcvo /data /app
USER rcvo
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app/src PORT=8080
VOLUME ["/data"]
EXPOSE 8080
CMD ["python","/app/src/service.py","--state-db","/data/prospecting-agent.sqlite","--campaign","/app/config/campaign.json","--mailboxes","/app/config/mailboxes.json","--identity","/app/config/identity.json"]
