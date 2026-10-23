FROM python:3.12-slim
WORKDIR /app
COPY server.py .
COPY public ./public
ENV HOST=0.0.0.0 PORT=8000 ECO_DATA=/data COOKIE_SECURE=1
VOLUME /data
EXPOSE 8000
CMD ["python3", "server.py"]
