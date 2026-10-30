FROM python:3.12-slim

WORKDIR /app

# System dependencies for psycopg2, OpenCV
RUN apt-get update && apt-get install -y \
    gcc libpq-dev libgl1-mesa-glx libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-ai.txt ./
RUN pip install --no-cache-dir -r requirements-ai.txt

COPY . .

RUN python manage.py collectstatic --noinput || true

EXPOSE 8000

CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
