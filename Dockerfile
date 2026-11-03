FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY ecommerce ./ecommerce
COPY frontend ./frontend
COPY data ./data
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "ecommerce", "principal", "--host", "0.0.0.0"]
