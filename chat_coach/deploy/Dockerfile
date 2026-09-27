# Контекст сборки — папка chat_coach:
#   docker build -f deploy/Dockerfile -t chat-coach .
FROM python:3.11-slim

# Логи должны идти в docker logs сразу, а не лежать в буфере до падения.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# База живёт на примонтированном томе, а не внутри контейнера: пересборка
# образа не должна стирать счётчики пользователей.
ENV CHAT_COACH_DB=/data/bot.sqlite3
RUN mkdir -p /data && \
    useradd --create-home --uid 10001 coach && \
    chown -R coach /app /data
USER coach

CMD ["python", "bot.py"]
