FROM python:3.13-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app

RUN pip install --no-cache-dir fastmcp pyjwt

COPY shared /app/shared
COPY services/pharmacy /app/services/pharmacy

EXPOSE 8002
CMD ["python", "services/pharmacy/server.py"]
