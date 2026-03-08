FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install ffmpeg — required by whisper_service for audio extraction
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run Uvicorn server explicitly, allowing Cloud Run to inject its dynamically assigned PORT.
# Default back to 8080 if PORT is not set.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}
