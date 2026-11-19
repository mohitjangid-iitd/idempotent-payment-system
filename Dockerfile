FROM python:3.14-slim

WORKDIR /app

# pehle sirf requirements, taaki code badalne par libraries dobara install na ho
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# 0.0.0.0 zaroori hai, warna container ke bahar se koi connect nahi kar sakta
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]