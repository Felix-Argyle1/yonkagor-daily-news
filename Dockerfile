FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY youtube_discord_bot.py .

CMD ["python", "youtube_discord_bot.py"]
