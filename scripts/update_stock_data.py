#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
股票買入時機小助手 —— 股票清單 + 行情自動更新（GitHub Actions 版）

用法：
    python3 scripts/update_stock_data.py index.html

流程：
  1. 以 repo 根目錄的 stocks.json 作為「股票清單」唯一來源（沒有就從 index.html 的 DATA 讀出來建檔）
  2. 抓每隻股票的收市價與日 K，算出 MA5 / MA20 / RSI(14) / 20 日支撐壓力
  3. 把計算結果寫回 stocks.json，並把整份 DATA 重新嵌入 index.html（新增的股票即時可被「查信號 / 觀察名單」使用）
  4. 更新頁腳「數據更新於 X年X月X日（收市）」

不會改動任何操作邏輯、界面、分組卡片與你自己設定的目標買入價 target。
"""
import sys, os, re, json, time, urllib.request, datetime

HKT = datetime.timezone(datetime.timedelta(hours=8))


def get(url, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0 (compatible; stock-helper-bot)",
                              "Referer": "https://gu.qq.com/"})
            return urllib.request.urlopen(req, timeout=20).read()
        except Exception as e:
            last = e
            time.sleep(2 + 2 * i)
    raise last


def fetch_quotes(codes):
    out = {}
    for i in range(0, len(codes), 20):
        chunk = codes[i:i + 20]
        raw = get("https://qt.gtimg.cn/q=" + ",".join("hk" + c for c in chunk)).decode("gbk", "ignore")
        for line in raw.split(";"):
            line = line.strip()
            if not line.startswith("v_hk"):
                continue
            f = line.split("=", 1)[1].strip().strip('"').split("~")
            if len(f) < 40:
                continue
            k = next((j for j, x in enumerate(f) if re.match(r"^\d{4}/\d{2}/\d{2}", x)), None)
            if k is None:
                continue
            out[f[2].zfill(5)] = {"name": f[1], "price": float(f[3]),
                                  "high": float(f[k + 3]), "low": float(f[k + 4]), "time": f[k]}
    return out


def fetch_kline(code, n=90):
    url = "https://web.ifzq.gtimg.cn/appstock/app/hkfqkline/get?param=hk%s,day,,,%d,qfq" % (code, n)
    data = json.loads(get(url).decode("utf-8", "ignore"))
    node = data["data"]["hk" + code]
    rows = node.get("day") or node.get("qfqday") or []
    return ([float(r[2]) for r in rows], [float(r[3]) for r in rows], [float(r[4]) for r in rows])


def rsi14(closes, period=14):
    if len(closes) < period + 1:
        return 50
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        ag = (ag * (period - 1) + gains[i]) / period
        al = (al * (period - 1) + losses[i]) / period
    if al == 0:
        return 100
    return round(100 - 100 / (1 + ag / al))


def load_list(html, json_path):
    """stocks.json 為準；沒有就由 index.html 的 DATA 建立。"""
    if os.path.exists(json_path):
        data = json.load(open(json_path, encoding="utf-8"))
        return data.get("stocks", []), data
    m = re.search(r"var DATA = (\{.*?\});\s*\n\s*var cur", html, re.S)
    if not m:
        raise RuntimeError("找不到 DATA，亦沒有 stocks.json")
    old = json.loads(m.group(1))
    stocks = []
    for code, v in old.items():
        stocks.append({"code": code, "name": v.get("name", code), "target": v.get("target"),
                       "y": v.get("y", "—"), "grp": v.get("grp", "自選"),
                       "note": v.get("note", ""), "news": v.get("news", "")})
    return stocks, {"updated": "", "stocks": stocks}


def enrich(stocks, quotes):
    for s in stocks:
        s["code"] = str(s["code"]).zfill(5)
        code = s["code"]
        q = quotes.get(code)
        if not q:
            print("⚠ 抓不到 %s 行情，沿用舊值" % code)
            continue
        closes, highs, lows = fetch_kline(code)
        cur = q["price"]
        if not closes or abs(closes[-1] - cur) > 0.01:
            closes += [cur]
            highs += [q["high"]]
            lows += [q["low"]]
        if not s.get("name"):
            s["name"] = q["name"]
        if s.get("target") in (None, "", 0):
            s["target"] = round(cur * 0.97, 2)
            s["note"] = (s.get("note") or "") + "（自動目標：現價 −3%，可自行改 target）"
        s.setdefault("y", "—")
        s.setdefault("grp", "自選")
        s.setdefault("note", "自訂加入")
        s.setdefault("news", "（未設定當天消息，可在 stocks.json 補上）")
        s["price"] = cur
        s["ma5"] = sum(closes[-5:]) / 5
        s["ma20"] = sum(closes[-20:]) / 20
        s["rsi"] = rsi14(closes)
        s["sup"] = min(lows[-20:])
        s["res"] = max(highs[-20:])
    return stocks


def to_data_literal(stocks):
    obj = {}
    for s in stocks:
        obj[s["code"]] = {
            "name": s["name"], "price": round(float(s["price"]), 3), "target": s["target"],
            "y": s["y"], "grp": s["grp"], "note": s["note"],
            "ma5": round(s["ma5"], 3), "ma20": round(s["ma20"], 3), "rsi": int(s["rsi"]),
            "sup": round(s["sup"], 3), "res": round(s["res"], 3), "news": s["news"],
        }
    return json.dumps(obj, ensure_ascii=False, separators=(", ", ": "))


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "index.html"
    root = os.path.dirname(os.path.abspath(path))
    json_path = sys.argv[2] if len(sys.argv) > 2 else os.path.join(root, "stocks.json")
    html = open(path, encoding="utf-8").read()

    stocks, doc = load_list(html, json_path)
    codes = [str(s["code"]).zfill(5) for s in stocks]
    quotes = fetch_quotes(codes)
    stocks = enrich(stocks, quotes)

    literal = to_data_literal(stocks)
    new_html, n = re.subn(r"var DATA = \{.*?\};\s*\n(\s*)var cur",
                          lambda m: "var DATA = " + literal + ";\n" + m.group(1) + "var cur",
                          html, count=1, flags=re.S)
    if n == 0:
        raise RuntimeError("無法在 index.html 找到 DATA，停止以免弄壞頁面")

    now = datetime.datetime.now(HKT)
    stamp = "%d年%d月%d日" % (now.year, now.month, now.day)
    new_html = re.sub(r"(生成於|數據更新於)\s*\d{4}年\d{1,2}月\d{1,2}日(（收市）)?",
                      "數據更新於 " + stamp + "（收市）", new_html, count=1)

    changed = new_html != html
    if changed:
        open(path, "w", encoding="utf-8").write(new_html)

    doc["updated"] = now.strftime("%Y-%m-%d %H:%M HKT")
    doc["stocks"] = stocks
    json.dump(doc, open(json_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    print("stocks.json 已同步：%d 隻" % len(stocks))
    print("狀態：%s" % ("已更新 index.html" if changed else "價格無變化，未改動 index.html"))
    for s in stocks:
        print("%s %-8s 現價 %.3f  目標 %s  MA20 %.2f  RSI %d"
              % (s["code"], s["name"], s["price"], s["target"], s["ma20"], s["rsi"]))
    print("::notice::完成，共 %d 隻股票" % len(stocks))


if __name__ == "__main__":
    main()
