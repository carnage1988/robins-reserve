FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN groupadd --system robinsreserve \
    && useradd \
       --system \
       --gid robinsreserve \
       --home-dir /app \
       robinsreserve

COPY requirements.txt .

RUN pip install \
    --no-cache-dir \
    --upgrade pip \
    && pip install \
       --no-cache-dir \
       -r requirements.txt

COPY bot.py config.py sheets_service.py league_service.py ./

RUN mkdir -p /app/data /app/logs /app/secrets \
    && chown -R robinsreserve:robinsreserve /app

USER robinsreserve

CMD ["python", "bot.py"]
