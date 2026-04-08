FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System deps (if feedparser/aiohttp ever need them)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first (better layer caching)
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# Copy the Python source used at runtime
COPY bot.py ./
COPY ai_news_bot.py ./
COPY news_core.py ./

# Default location for runtime state
VOLUME ["/app/state"]

# Use /app as CWD; archive and dedupe files live in /app/state
CMD ["python", "bot.py"]

