FROM python:3.12-slim

RUN groupadd --gid 10002 event \
    && useradd --uid 10002 --gid 10002 --home-dir /data/event --no-create-home event \
    && install -d -o 10002 -g 10002 /app /data/event

WORKDIR /app
COPY deploy/event_gateway/app.py /app/app.py

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 EVENT_DB=/data/event/event.sqlite
USER 10002:10002
CMD ["python", "/app/app.py"]
