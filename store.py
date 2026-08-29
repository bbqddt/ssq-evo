# -*- coding: utf-8 -*-
"""SQLite 持久化：每轮 run 摘要 + 每个算子评估 + 全局 leaderboard。"""
import os, sqlite3, datetime, json
import ssq_log


def open_db(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    con = sqlite3.connect(path)
    cur = con.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS runs(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT, n_issues INTEGER, added INTEGER,
        n_eval INTEGER, best_q REAL, best_sig TEXT, best_test TEXT,
        best_p REAL, oos_p REAL, alert INTEGER, note TEXT)""")
    # 增量迁移：新增 coverage 列（已存在则忽略）
    try:
        cur.execute("ALTER TABLE runs ADD COLUMN coverage INTEGER")
    except sqlite3.OperationalError as _e:
        ssq_log.log_exception("store", _e, "store.py:18 silent-except")
    cur.execute("""CREATE TABLE IF NOT EXISTS evals(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER, gen INTEGER, sig TEXT, test TEXT, tier TEXT, direction TEXT,
        stat REAL, sur_mean REAL, sur_std REAL, z REAL, p_raw REAL, q REAL,
        k_sur INTEGER, verdict TEXT)""")
    con.commit()
    return con


def insert_run(con, run):
    cur = con.cursor()
    cur.execute("""INSERT INTO runs(ts,n_issues,added,n_eval,best_q,best_sig,best_test,
        best_p,oos_p,alert,note,coverage) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (run["ts"], run["n_issues"], run["added"], run["n_eval"],
         run["best_q"], run["best_sig"], run["best_test"],
         run["best_p"], run["oos_p"], int(run["alert"]), run["note"],
         int(run.get("coverage", 0))))
    rid = cur.lastrowid
    con.commit()
    return rid


def insert_evals(con, run_id, evals):
    cur = con.cursor()
    rows = [(run_id, e.get("gen", 0), e["sig"], e["test"], e["tier"], e["direction"],
             e["stat"], e["sur_mean"], e["sur_std"], e["z"], e["p_raw"], e.get("q", 1.0),
             e["k_sur"], e["verdict"]) for e in evals]
    cur.executemany("""INSERT INTO evals(run_id,gen,sig,test,tier,direction,stat,
        sur_mean,sur_std,z,p_raw,q,k_sur,verdict) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
    con.commit()


def recent_runs(con, limit=200):
    cur = con.cursor()
    cur.execute("SELECT id,ts,n_issues,added,best_q,best_sig,best_test,best_p,oos_p,alert,coverage FROM runs ORDER BY id DESC LIMIT ?", (limit,))
    return cur.fetchall()


def latest_run(con):
    cur = con.cursor()
    cur.execute("SELECT id,ts,n_issues,best_q,best_sig,best_test,alert,oos_p FROM runs ORDER BY id DESC LIMIT 1")
    return cur.fetchone()
