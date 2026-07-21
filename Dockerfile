FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements_fleet.txt ./
RUN pip install --upgrade pip && pip install -r requirements_fleet.txt

COPY . .

EXPOSE 8090
EXPOSE 3001

ENTRYPOINT ["python", "run_fleet.py"]
CMD ["--insecure", "--control-port", "8090", "--no-persist"]
