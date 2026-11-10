FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates nodejs \
       tesseract-ocr tesseract-ocr-ara \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/generated_music /app/logs /app/runtime_state/talkin

EXPOSE 8080

# BOT_MODE=talkin (الافتراضي) يشغّل بوت Talkin الكامل على جينات شات.
# BOT_MODE=alsfer يشغّل بوت السفير الأصلي في bot.py.
CMD ["python", "start.py"]
