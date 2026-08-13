# -*- coding: utf-8 -*-
"""双色球数据：增量抓取 (500彩票网) + 本地主表按期号合并去重。

注意：500 历史表结构为 [标记, 期号, 红1..红6, 蓝]，无独立日期列；
因此以 5 位零填充期号 (如 03001 / 26092) 作为单调递增主键做增量合并。
"""
import os, re, csv, urllib.request

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


def load_master(path):
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rows.append(row)
    return rows


def update_master(master, fresh):
    """把 fresh 中期号大于 master 最新期号的行并入；返回(新主表, 新增数)。"""
    if not master:
        master = [{"issue": r[0], "r1": r[1], "r2": r[2], "r3": r[3],
                   "r4": r[4], "r5": r[5], "r6": r[6], "b": r[7]} for r in fresh]
        return master, len(fresh)
    last_issue = master[-1]["issue"]
    added = 0
    for r in fresh:
        if r[0] > last_issue:
            master.append({"issue": r[0], "r1": r[1], "r2": r[2], "r3": r[3],
                           "r4": r[4], "r5": r[5], "r6": r[6], "b": r[7]})
            added += 1
    return master, added


def save_master(master, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["issue", "r1", "r2", "r3", "r4", "r5", "r6", "b"])
        w.writeheader()
        w.writerows(master)


def to_arrays(master):
    reds = __import__("numpy").array([[int(m[f"r{i}"]) for i in range(1, 7)] for m in master])
    blues = __import__("numpy").array([int(m["b"]) for m in master])
    issues = [m["issue"] for m in master]
    return reds, blues, issues
