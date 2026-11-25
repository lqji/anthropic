FROM python:3.13-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app

RUN pip install --no-cache-dir fastapi uvicorn pyjwt httpx pydantic langgraph fastmcp

COPY shared /app/shared
COPY services /app/services
COPY gateway /app/gateway

EXPOSE 8000
CMD ["python", "gateway/main.py"]
