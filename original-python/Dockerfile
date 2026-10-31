FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/data COOKIE_SECURE=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd -u 10001 -m showroom && mkdir /data && chown showroom:showroom /data
COPY --chown=showroom:showroom . .
USER showroom
VOLUME ["/data"]
EXPOSE 8088
CMD ["gunicorn", "--bind", "0.0.0.0:8088", "--workers", "2", "--threads", "4", "--timeout", "300", "--access-logfile", "-", "app:application"]
