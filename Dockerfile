# Dockerfile for ssq_evo 7x24 structure-search engine
FROM python:3.13-slim

RUN pip install --no-cache-dir numpy scipy && \
    mkdir /app

WORKDIR /app
COPY engine_core.py data.py store.py run_cycle.py serve.py daemon_loop.py frontier.py make_dashboard.py config.json ./

# 默认入口：常驻循环（每 schedule_hours 小时跑一轮）
CMD ["python", "-u", "daemon_loop.py"]
