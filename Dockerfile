FROM python:3.12-slim

WORKDIR /app
ENV PYTHONPATH=/app

# System dependencies for PDF parsing, web scraping, and Playwright
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    # Playwright/Chromium dependencies
    libnss3 \
    libatk-bridge2.0-0 \
    libdrm2 \
    libxkbcommon0 \
    libgbm1 \
    libasound2 \
    libxshmfence1 \
    libxrandr2 \
    libxfixes3 \
    libxdamage1 \
    libxcomposite1 \
    libatk1.0-0 \
    libcups2 \
    libpango-1.0-0 \
    libcairo2 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright Chromium browser (deps already installed above)
RUN playwright install chromium

COPY . .

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
