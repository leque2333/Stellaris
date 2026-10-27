FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV CONSOLE_PORT=18890

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

COPY . /app

EXPOSE 18890

CMD ["python", "server.py"]
