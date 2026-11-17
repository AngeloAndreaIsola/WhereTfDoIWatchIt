FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

COPY worker-entrypoint.sh /worker-entrypoint.sh
RUN chmod +x /worker-entrypoint.sh

COPY guarded-migrate-entrypoint.sh /guarded-migrate-entrypoint.sh
RUN chmod +x /guarded-migrate-entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]
