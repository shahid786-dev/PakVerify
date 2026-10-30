FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    gcc libgl1-mesa-glx libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-full.txt ./
RUN pip install --no-cache-dir -r requirements-full.txt

COPY . .

EXPOSE 8001

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
