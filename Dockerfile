ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE}
WORKDIR /app
COPY pyproject.toml ./
COPY app ./app
COPY worker ./worker
COPY config ./config
COPY alembic ./alembic
COPY alembic.ini ./
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple .
RUN mkdir -p /app/data
EXPOSE 8888
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8888"]
