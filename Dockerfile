# Dockerfile for ssq_evo 7x24 structure-search engine
FROM python:3.13-slim

RUN pip install --no-cache-dir numpy scipy && \
    mkdir /app

WORKDIR /app
COPY engine_core.py data.py store.py run_cycle.py serve.py daemon_loop.py frontier.py make_dashboard.py nonstationarity.py evaluator.py cache.py diff_formula.py positive_control.py redteam_audit.py representation_zoo.py layered_null.py run_axes.py firewall.py proposer.py scoring.py formula_viz.py predict_tonight.py config.json benchmark_speed.py smoke_test.py verify_firewall.py pre_commit_check.py verify_deployment.py verify_automation_reachability.py ./
COPY configs ./configs

# 默认入口：常驻循环（每 schedule_hours 小时跑一轮）
CMD ["python", "-u", "daemon_loop.py"]
