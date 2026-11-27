FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/data
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin portal \
    && install -d -o portal -g portal /data \
    && touch /data/.volume \
    && chown portal:portal /data/.volume
COPY manage.py .
COPY portal ./portal
COPY output/pdf/*.pdf ./output/pdf/
USER portal
EXPOSE 8000
CMD ["sh", "-ec", "python manage.py init_db && exec gunicorn --no-control-socket --workers 1 --threads 4 --bind 0.0.0.0:8000 portal.wsgi:application"]
