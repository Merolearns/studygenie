FROM python:3.12-slim

WORKDIR /app

# install dependencies first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# the database lives here; mount a volume for durability
ENV STUDYGENIE_DB=/data/studygenie.db
VOLUME /data

# run migrations + start (migrate runs on first request via before_request too)
EXPOSE 8000
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "app:app"]
