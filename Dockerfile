FROM python:3.13-slim
WORKDIR /app
COPY bridge.py /app/bridge.py
COPY prompts /app/prompts
RUN useradd --uid 10001 --create-home bridge && mkdir /data && chown bridge:bridge /data
USER bridge
EXPOSE 8787
HEALTHCHECK --interval=30s --timeout=3s CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/health', timeout=2)"
CMD ["python3", "bridge.py", "--host", "0.0.0.0", "--config", "/app/fleet.json", "--db", "/data/bridge.sqlite3", "--env-file", "/dev/null"]
