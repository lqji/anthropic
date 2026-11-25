FROM python:3.13-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app

RUN pip install --no-cache-dir fastmcp pyjwt

COPY shared /app/shared
COPY services/records /app/services/records

EXPOSE 8003
CMD ["python", "services/records/server.py"]
