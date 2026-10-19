FROM python:3.12-slim
WORKDIR /app
RUN useradd --uid 10001 --create-home tracker && mkdir /data && chown tracker:tracker /data
COPY server.py index.html app.js style.css /app/
USER tracker
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
EXPOSE 8777
CMD ["python", "server.py", "--host", "0.0.0.0", "--db", "/data/cases.sqlite3"]
