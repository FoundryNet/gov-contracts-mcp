FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Railway injects PORT; default 8080 for local runs.
ENV PORT=8080
EXPOSE 8080

CMD ["python", "server.py"]
