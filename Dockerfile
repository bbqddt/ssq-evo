# Dockerfile for ssq_evo 7x24 structure-search engine
FROM python:3.13-slim

# 构建时注入 git SHA，用于"容器是否跑旧码"自动检测（verify_deployment 比对）。
# 重建命令：GIT_SHA=$(git -C D:/ssq_evo rev-parse HEAD) docker compose up -d --build
ARG GIT_SHA=unknown

RUN pip install --no-cache-dir numpy scipy && \
    mkdir /app

WORKDIR /app
COPY engine_core.py evolve_predictor.py data.py store.py run_cycle.py serve.py daemon_loop.py frontier.py make_dashboard.py nonstationarity.py evaluator.py cache.py diff_formula.py positive_control.py redteam_audit.py representation_zoo.py layered_null.py run_axes.py firewall.py proposer.py scoring.py formula_viz.py predict_tonight.py config.json benchmark_speed.py smoke_test.py verify_firewall.py pre_commit_check.py verify_deployment.py verify_automation_reachability.py ssq_health.py ci_evolve.py data_refresh.py ingest_candidates.py merge_candidates.py learning_contract.py failure_absorber.py axis_proposer.py review_primitives.py bias_corrector.py formula_composer.py formula_research.py watchdog_mode.py seed_bridge.py verify_df_gen.py progress_gate.py blue_evolve.py changepoint_evolve.py gru_evolve.py seq_evolve.py novelty_search.py reflective_designer.py ghost_hunter.py paths.py ssq_log.py pattern_audit.py honesty_footer.py verdict_card.py analysis_ledger.py ./
COPY configs ./configs

# 构建期三重门禁：任何一类"隐藏问题"都会让 build 直接失败。
# ① ghost_hunter  : import 了但文件不存在的幽灵模块
# ② pattern_audit : 代码坏模式（with 块外 flush、静默 except、硬编码盘符 ...）
# ③ import 冒烟   : import 期错误 / 漏拷 .py
# 根治"容器跑旧码/缺模块/带病上线"在部署前被发现（CI docker-build 也依赖它）。
RUN python ghost_hunter.py \
 && python pattern_audit.py --strict \
 && python -c "import paths,ssq_log,pattern_audit,engine_core,evolve_predictor,data,store,run_cycle,daemon_loop,frontier,make_dashboard,nonstationarity,evaluator,cache,diff_formula,positive_control,redteam_audit,representation_zoo,layered_null,run_axes,firewall,proposer,scoring,formula_viz,predict_tonight,ssq_health,learning_contract,failure_absorber,ingest_candidates,axis_proposer,review_primitives,bias_corrector,formula_composer,formula_research,watchdog_mode,seed_bridge,verify_df_gen,progress_gate,blue_evolve,changepoint_evolve,gru_evolve,seq_evolve,novelty_search,reflective_designer,honesty_footer,verdict_card,analysis_ledger; print('IMPORT_OK')"

# 把构建 SHA 落盘，供 daemon 启动打印 + verify 比对（根治"改了代码没重建"）。
RUN echo "$GIT_SHA" > /app/build_info.txt
LABEL ssq.git_sha="$GIT_SHA"

# 默认入口：常驻循环（每 schedule_hours 小时跑一轮）
CMD ["python", "-u", "daemon_loop.py"]
