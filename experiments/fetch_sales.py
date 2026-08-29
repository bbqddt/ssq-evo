# -*- coding: utf-8 -*-
"""
fetch_sales.py —— 回填双色球「销售额 / 奖池」外生历史（突破真杠杆）
==============================================================
数据源：中彩网官方接口（每期公开，且销售额/奖池在停售 20:00 即确定，
先于开奖 21:15，是真正「外生于机械摇奖」的观测）。

接口：https://www.cwl.gov.cn/cwl_admin/front/cwlkj/search/kjxx/findDrawNotice
参数：name=ssq, issueStart, issueEnd, pageNo, pageSize, systemType=PC
返回 JSON：result[].code(7位期号) / date / sales / poolmoney ...

输出：D:/ssq_evo_data/ssq_sales.csv
      列：code5(5位期号，与 ssq_master.csv 对齐), date, sales, poolmoney
说明：5 位期号 = cwl 7 位期号末 5 位（YYYYNNN -> YYNNN），与 500 网 master 一致。

诚信说明：本脚本只抓取公开开奖公告元数据，不改写任何引擎逻辑；
销售额/奖池仅作为 NEW 外生信号，须经 formula_research_sales.py 的诚实闸门评测，
绝不自动合并进演进（红线）。
"""
import os, csv, json, sys, time, urllib.request, urllib.parse

API = "https://www.cwl.gov.cn/cwl_admin/front/cwlkj/search/kjxx/findDrawNotice"
OUT = "D:/ssq_evo_data/ssq_sales.csv"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36")

PROXY = (os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
         or "http://127.0.0.1:10808/")


def _opener():
    ph = urllib.request.ProxyHandler({"http": PROXY, "https": PROXY})
    return urllib.request.build_opener(ph)


def fetch_chunk(issue_start, issue_end, opener, page=1, page_size=300):
    qs = urllib.parse.urlencode({
        "name": "ssq", "issueStart": issue_start, "issueEnd": issue_end,
        "pageNo": str(page), "pageSize": str(page_size), "systemType": "PC",
    })
    url = API + "?" + qs
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Referer": "https://www.cwl.gov.cn/"})
    with opener.open(req, timeout=40) as resp:
        raw = resp.read().decode("utf-8", "ignore")
    obj = json.loads(raw)
    if obj.get("state") != 0:
        raise RuntimeError("API state=%s msg=%s" % (obj.get("state"), obj.get("message")))
    return obj.get("result", [])


def fetch_all():
    opener = _opener()
    rows = {}
    # 按自然年分块（每年 ~156 期），避免单次分页截断；覆盖 2003..2026
    for yy in range(2003, 2027):
        start = "%d001" % yy
        end = "%d999" % yy
        try:
            page = 1
            while True:
                chunk = fetch_chunk(start, end, opener, page=page, page_size=300)
                if not chunk:
                    break
                for r in chunk:
                    code7 = str(r.get("code", "")).strip()
                    if len(code7) < 5:
                        continue
                    code5 = code7[-5:]
                    sales = r.get("sales", "")
                    pool = r.get("poolmoney", "")
                    if sales in (None, ""):
                        sales = ""
                    if pool in (None, ""):
                        pool = ""
                    rows[code5] = {
                        "code5": code5,
                        "date": str(r.get("date", ""))[:10],
                        "sales": str(sales),
                        "poolmoney": str(pool),
                    }
                if len(chunk) < 300:
                    break
                page += 1
                time.sleep(0.15)
        except Exception as e:
            print("  [warn] 年份 %d 抓取异常: %s" % (yy, e))
            continue
        print("  [ok] 年份 %d 累计 %d 行" % (yy, len(rows)))
        time.sleep(0.2)
    return rows


def save(rows, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["code5", "date", "sales", "poolmoney"])
        w.writeheader()
        for k in sorted(rows.keys()):
            w.writerow(rows[k])


def main():
    print("[fetch_sales] 开始回填双色球销售额/奖池历史（代理: %s）" % PROXY)
    rows = fetch_all()
    if not rows:
        print("[fetch_sales] 未抓到任何数据，请检查网络/代理。")
        sys.exit(1)
    save(rows, OUT)
    # 简单统计
    nums = [int(v["sales"]) for v in rows.values() if v["sales"].isdigit()]
    pools = [int(v["poolmoney"]) for v in rows.values() if v["poolmoney"].isdigit()]
    print("[fetch_sales] 写入 %d 期 -> %s" % (len(rows), OUT))
    print("  sales 有值 %d 期, pool 有值 %d 期" % (len(nums), len(pools)))
    if nums:
        print("  sales 范围: %d ~ %d" % (min(nums), max(nums)))
    if pools:
        print("  pool  范围: %d ~ %d" % (min(pools), max(pools)))


if __name__ == "__main__":
    main()
