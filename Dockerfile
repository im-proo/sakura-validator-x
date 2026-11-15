FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TEMP_DIR=/tmp/sakura

WORKDIR /app

# Dependencies first so code edits reuse the cached layer.
COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

# Run unprivileged; the bot writes uploaded cookie files to TEMP_DIR.
RUN useradd --create-home --uid 10001 sakura \
    && mkdir -p "$TEMP_DIR" \
    && chown -R sakura:sakura /app "$TEMP_DIR"

USER sakura

# Configuration comes from the environment — never bake .env into the image.
# Required: BOT_TOKEN, MONGO_URI, OWNER_ID
CMD ["python", "bot.py"]
