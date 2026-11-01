FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 VISENTRA_DATA_DIR=/data
WORKDIR /app
COPY requirements.txt .
# CPU wheels by default; for CUDA use the matching index from pytorch.org.
RUN pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu -r requirements.txt
COPY pyproject.toml ./
COPY apps ./apps
COPY ml ./ml
# the operational detector and the aircraft-surface gate (weights are git-ignored: build from a checkout that has them)
COPY models/detection/VT-DET-0002 ./models/detection/VT-DET-0002
COPY models/gate/VT-GATE-0002 ./models/gate/VT-GATE-0002
RUN pip install --no-cache-dir --no-deps -e . && python apps/web/build.py && useradd -m -u 10001 visentra && mkdir -p /data && chown visentra /data
USER visentra
EXPOSE 8000
CMD ["uvicorn", "visentra_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
