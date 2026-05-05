FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app/Linux/CdiCoreMain/TelemetryGrpcClient

COPY Linux/CdiCoreMain/TelemetryGrpcClient/requirements_fleet.txt ./
RUN pip install --upgrade pip && pip install -r requirements_fleet.txt

COPY Linux/CdiCoreMain/TelemetryGrpcClient/ ./
COPY Linux/CdiCoreMain/src/Telemetry/proto/telemetry.proto /app/Linux/CdiCoreMain/src/Telemetry/proto/telemetry.proto
RUN python -m grpc_tools.protoc \
    -I/app/Linux/CdiCoreMain/src/Telemetry/proto \
    --python_out=/app/Linux/CdiCoreMain/src/Telemetry/proto \
    --grpc_python_out=/app/Linux/CdiCoreMain/src/Telemetry/proto \
    /app/Linux/CdiCoreMain/src/Telemetry/proto/telemetry.proto

EXPOSE 8090

ENTRYPOINT ["python", "run_fleet.py"]
CMD ["--insecure", "--control-port", "8090", "--no-persist"]
