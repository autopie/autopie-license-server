FROM python:3.12-slim

WORKDIR /app
COPY crypto_rs256.py license_core.py web_ui.py server.py ./

ENV HOST=0.0.0.0
ENV PORT=8790
ENV DATA_DIR=/data

VOLUME ["/data"]
EXPOSE 8790

CMD ["python", "-u", "server.py"]
