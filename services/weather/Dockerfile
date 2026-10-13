FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml .
COPY hestia_weather ./hestia_weather

RUN pip install --no-cache-dir .

CMD ["python", "-m", "hestia_weather.runner"]
