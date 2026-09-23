FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml ./
COPY app ./app
COPY deploy ./deploy
RUN pip install --no-cache-dir . && useradd --uid 10001 --create-home explorer && mkdir /data && chown explorer:explorer /data
USER 10001:10001
ENV APP_MODE=remote DATABASE_URL=sqlite:////data/remote.sqlite3
EXPOSE 8765
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8765", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "127.0.0.1,172.30.87.1", "--no-access-log"]
