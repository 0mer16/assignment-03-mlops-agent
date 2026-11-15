# Pinned base image so the build is reproducible.
FROM python:3.13.5-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MODEL_PATH=/app/models/model.joblib

WORKDIR /app

# Dependencies first: this layer is cached while only the code changes.
COPY api/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# The trained model is baked into the image so a pod needs no shared volume.
COPY models/model.joblib ./models/model.joblib
COPY api/ ./api/

RUN useradd --create-home --uid 10001 appuser && chown -R appuser /app
# Numeric, not "appuser": with runAsNonRoot set, the kubelet has to verify the
# user is not root before the container starts, and it cannot resolve a name.
USER 10001

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
