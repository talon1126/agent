FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e .
COPY rag/pyproject.toml rag/README.md rag/main.py ./rag/
COPY rag/config ./rag/config
COPY rag/src ./rag/src
COPY rag/data ./rag/data
RUN pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cu128 -e "./rag[rerank,intent-lora]"
COPY app ./app
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
