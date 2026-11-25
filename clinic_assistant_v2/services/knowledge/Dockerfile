FROM python:3.13-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app

RUN pip install --no-cache-dir fastmcp pyjwt qdrant-client openai

COPY shared /app/shared
COPY services/knowledge /app/services/knowledge

EXPOSE 8004
CMD ["python", "services/knowledge/server.py"]
