# -*- coding: utf-8 -*-
"""双色球数据：增量抓取 (500彩票网) + 本地主表按期号合并去重。

注意：500 历史表结构为 [标记, 期号, 红1..红6, 蓝]，无独立日期列；
因此以 5 位零填充期号 (如 03001 / 26092) 作为单调递增主键做增量合并。
"""
import os, re, csv, urllib.request

import ssq_log

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
REF = "https://datachart.500.com/ssq/"
FETCH_URL = "https://datachart.500.com/ssq/history/newinc/history.php?start=03001&end=29999"


def parse_html(html_text):
    """解析出 list[(issue, r1..r6, b)]，按期号升序。"""
    m = re.search(r'<tbody[^>]*id="tdata"[^>]*>(.*?)</tbody>', html_text, re.S)
    if not m:
        m = re.search(r'<div[^>]*id="tdata"[^>]*>(.*?)</div>', html_text, re.S)
    body = m.group(1) if m else html_text
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', body, re.S)
    data = []
    for r in rows:
        tds = re.findall(r'<td[^>]*>(.*?)</td>', r, re.S)
        if not tds:
            continue
        cells = [re.sub(r'<[^>]+>', '', c).strip() for c in tds]
        # 结构: [标记, 期号, 红1..红6, 蓝] 最少 9 列
        if len(cells) < 9 or not cells[1].isdigit():
            continue
        issue = cells[1]
        reds = cells[2:8]
        blue = cells[8]
        if len(reds) == 6 and all(x.isdigit() for x in reds) and blue.isdigit():
            data.append([issue] + reds + [blue])
    data.sort(key=lambda x: x[0])
    return data


def fetch_recent():
    try:
        req = urllib.request.Request(FETCH_URL, headers={"User-Agent": UA, "Referer": REF})
        with urllib.request.urlopen(req, timeout=40) as resp:
            raw = resp.read().decode("utf-8", "ignore")
        return parse_html(raw)
    except Exception as e:
        print("[data] fetch failed:", e)
        return None


_FIELDS = ["issue", "r1", "r2", "r3", "r4", "r5", "r6", "b"]


def _issue_key(issue):
    """期号排序键：等长字符串直接比，长度不同时按数值比（防 '999' > '1000'）。"""
    s = str(issue).strip()
    return (len(s), s) if not s.isdigit() else (0, s.zfill(12))


def load_master(path):
    """读取主表。坏行（缺字段/空行）跳过并记日志，绝不整表崩。"""
    if not os.path.exists(path):
        return []
    rows = []
    bad = 0
    try:
        with open(path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if not row.get("issue"):
                    bad += 1
                    continue
                if any(row.get(k) in (None, "") for k in _FIELDS[1:]):
                    bad += 1
                    continue
                rows.append(row)
    except Exception as e:
        ssq_log.critical("data.load_master", f"master CSV unreadable: {path}", e)
        raise
    if bad:
        ssq_log.warn("data.load_master", f"skipped {bad} malformed row(s) in {path}")
    return rows


def update_master(master, fresh):
    """把 fresh 中期号大于 master 最大期号的行并入；返回(新主表, 新增数)。

    注意：不再假定 master 已按 issue 排序——用全局 max 而非 master[-1]，
    否则一旦主表顺序被打乱就会漏掉历史期号或重复插入。
    """
    if not master:
        master = [{"issue": r[0], "r1": r[1], "r2": r[2], "r3": r[3],
                   "r4": r[4], "r5": r[5], "r6": r[6], "b": r[7]} for r in fresh]
        return master, len(fresh)
    last_issue = max(_issue_key(m["issue"]) for m in master)
    added = 0
    for r in fresh:
        if _issue_key(r[0]) > last_issue:
            master.append({"issue": r[0], "r1": r[1], "r2": r[2], "r3": r[3],
                           "r4": r[4], "r5": r[5], "r6": r[6], "b": r[7]})
            added += 1
    return master, added


def save_master(master, path):
    """原子写主表。

    主表是全库唯一的历史数据源；非原子写一旦被 proc.kill/断电截断就等于
    永久丢失全部历史，因此必须 tmp + os.replace。
    """
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=_FIELDS)
        w.writeheader()
        w.writerows(master)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def to_arrays(master):
    """转 numpy 数组。单行数据非法时跳过该行并记日志，不整表崩。"""
    import numpy as np
    reds, blues, issues = [], [], []
    bad = 0
    for m in master:
        try:
            reds.append([int(m[f"r{i}"]) for i in range(1, 7)])
            blues.append(int(m["b"]))
            issues.append(m["issue"])
        except Exception as e:
            bad += 1
            if bad <= 3:
                ssq_log.warn("data.to_arrays",
                             f"skip malformed row issue={m.get('issue')!r}", e)
    if bad:
        ssq_log.warn("data.to_arrays", f"total {bad} malformed row(s) skipped")
    return np.array(reds), np.array(blues), issues
