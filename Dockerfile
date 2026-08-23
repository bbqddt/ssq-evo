# Dockerfile for ssq_evo 7x24 structure-search engine
FROM python:3.13-slim

# 构建时注入 git SHA，用于"容器是否跑旧码"自动检测（verify_deployment 比对）。
# 重建命令：GIT_SHA=$(git -C D:/ssq_evo rev-parse HEAD) docker compose up -d --build
ARG GIT_SHA=unknown

RUN pip install --no-cache-dir numpy scipy && \
    mkdir /app

WORKDIR /app
COPY engine_core.py data.py store.py run_cycle.py serve.py daemon_loop.py frontier.py make_dashboard.py nonstationarity.py evaluator.py cache.py diff_formula.py positive_control.py redteam_audit.py representation_zoo.py layered_null.py run_axes.py firewall.py proposer.py scoring.py formula_viz.py predict_tonight.py config.json benchmark_speed.py smoke_test.py verify_firewall.py pre_commit_check.py verify_deployment.py verify_automation_reachability.py ssq_health.py ci_evolve.py data_refresh.py ingest_candidates.py merge_candidates.py learning_contract.py failure_absorber.py axis_proposer.py review_primitives.py bias_corrector.py formula_composer.py verify_df_gen.py progress_gate.py ./
COPY configs ./configs

# 构建期冒烟测试：任何漏拷的 .py 或 import 期错误 → build 直接失败。
# 根治"容器跑旧码/缺模块"在部署前被发现（CI docker-build job 也依赖它）。
RUN python -c "import engine_core,data,store,run_cycle,daemon_loop,frontier,make_dashboard,nonstationarity,evaluator,cache,diff_formula,positive_control,redteam_audit,representation_zoo,layered_null,run_axes,firewall,proposer,scoring,formula_viz,predict_tonight,ssq_health,learning_contract,failure_absorber,ingest_candidates,axis_proposer,review_primitives,bias_corrector; print('IMPORT_OK')"

# 把构建 SHA 落盘，供 daemon 启动打印 + verify 比对（根治"改了代码没重建"）。
RUN echo "$GIT_SHA" > /app/build_info.txt
LABEL ssq.git_sha="$GIT_SHA"

# 默认入口：常驻循环（每 schedule_hours 小时跑一轮）
CMD ["python", "-u", "daemon_loop.py"]
