FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

WORKDIR /app

# Xvfb: virtual display so headless=False works without a real screen.
# NSE India blocks standard headless Chromium, so we run with a virtual display.
RUN apt-get update && apt-get install -y xvfb && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN playwright install chromium --with-deps

COPY scraper.py main.py ./

ENV PORT=8080
ENV DISPLAY=:99

EXPOSE 8080

# Start Xvfb (virtual display) then launch the app
CMD Xvfb :99 -screen 0 1280x800x24 -ac & python main.py
