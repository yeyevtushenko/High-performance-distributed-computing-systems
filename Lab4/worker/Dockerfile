FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends procps tini && rm -rf /var/lib/apt/lists/*
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY worker.py .
EXPOSE 9100
ENTRYPOINT ["tini", "--"]
CMD ["python", "worker.py"]
