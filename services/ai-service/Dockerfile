FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e .
COPY rag ./rag
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cu126 torch==2.12.1+cu126
RUN pip install --no-cache-dir -e "./rag[rerank]"
COPY app ./app
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
