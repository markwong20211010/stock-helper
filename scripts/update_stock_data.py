#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
股票買入時機小助手 —— 股價/指標自動更新腳本（GitHub Actions 版）

用法：
    python3 scripts/update_stock_data.py index.html

只更新 DATA 內的 price / ma5 / ma20 / rsi / sup / res 與頁腳「數據更新於」日期；
不會改動任何操作邏輯、目標買入價 target、股票清單、分組與界面文案。
"""
import sys, re, json, time, urllib.request, datetime

CODES = ["01810", "00700", "09988", "03690", "01398", "00939", "02318",
         "00883", "01088", "00941", "00002", "06823", "06181", "06862"]


def get(url, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0 (compatible; stock-helper-bot)",
                              "Referer": "https://gu.qq.com/"})
            return urllib.request.urlopen(req, timeout=20).read()
        except Exception as e:          # 網絡抖動重試
            last = e
            time.sleep(2 + 2 * i)
    raise last


def fetch_quotes(codes):
    q = ",".join("hk" + c for c in codes)
    raw = get("https://qt.gtimg.cn/q=" + q).decode("gbk", "ignore")
    out = {}
    for line in raw.split(";"):
        line = line.strip()
        if not line.startswith("v_hk"):
            continue
        f = line.split("=", 1)[1].strip().strip('"').split("~")
        if len(f) < 40:
            continue
        code = f[2]
        k = next((i for i, x in enumerate(f) if re.match(r"^\d{4}/\d{2}/\d{2}", x)), None)
        if k is None:
            continue
        out[code] = {"price": float(f[3]), "high": float(f[k + 3]), "low": float(f[k + 4]),
                     "time": f[k]}
    missing = [c for c in codes if c not in out]
    if missing:
        raise RuntimeError("行情缺漏：" + ",".join(missing))
    return out


def fetch_kline(code, n=90):
    url = "https://web.ifzq.gtimg.cn/appstock/app/hkfqkline/get?param=hk%s,day,,,%d,qfq" % (code, n)
    data = json.loads(get(url).decode("utf-8", "ignore"))
    node = data["data"]["hk" + code]
    rows = node.get("day") or node.get("qfqday") or []
    closes = [float(r[2]) for r in rows]
    highs = [float(r[3]) for r in rows]
    lows = [float(r[4]) for r in rows]
    return closes, highs, lows


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


def build_stats():
    quotes = fetch_quotes(CODES)
    stats = {}
    for code in CODES:
        closes, highs, lows = fetch_kline(code)
        cur = quotes[code]["price"]
        if not closes or abs(closes[-1] - cur) > 0.01:
            closes += [cur]
            highs += [quotes[code]["high"]]
            lows += [quotes[code]["low"]]
        stats[code] = {
            "price": cur,
            "ma5": sum(closes[-5:]) / 5,
            "ma20": sum(closes[-20:]) / 20,
            "rsi": rsi14(closes),
            "sup": min(lows[-20:]),
            "res": max(highs[-20:]),
        }
    return stats


def rewrite(html, stats):
    starts = [(m.start(), m.group(1)) for m in re.finditer(r'"(\d{5})":\s*\{', html)]
    if len(starts) < len(CODES):
        raise RuntimeError("页面內 DATA 結構不符合預期，停止更新以免弄壞頁面")
    pieces, cursor, changed = [], 0, False
    for idx, (pos, code) in enumerate(starts):
        end = starts[idx + 1][0] if idx + 1 < len(starts) else html.find("};", pos)
        pieces.append(html[cursor:pos])
        seg = html[pos:end]
        if code in stats:
            s, old = stats[code], seg
            for key, val in (("price", s["price"]), ("ma5", s["ma5"]), ("ma20", s["ma20"]),
                             ("sup", s["sup"]), ("res", s["res"])):
                txt = ("%.3f" % val).rstrip("0").rstrip(".")
                seg = re.sub(r'"%s":\s*[-0-9.]+' % key, '"%s": %s' % (key, txt), seg, count=1)
            seg = re.sub(r'"rsi":\s*[-0-9.]+', '"rsi": %d' % s["rsi"], seg, count=1)
            changed = changed or (seg != old)
        pieces.append(seg)
        cursor = end
    pieces.append(html[cursor:])
    new_html = "".join(pieces)
    if changed:
        today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
        new_html = re.sub(r"生成於\s*\d{4}年\d{1,2}月\d{1,2}日|數據更新於\s*\d{4}年\d{1,2}月\d{1,2}日(（收市）)?",
                          "數據更新於 %d年%d月%d日（收市）" % (today.year, today.month, today.day),
                          new_html, count=1)
    return new_html, changed


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "index.html"
    html = open(path, encoding="utf-8").read()
    stats = build_stats()
    new_html, changed = rewrite(html, stats)
    if changed:
        open(path, "w", encoding="utf-8").write(new_html)
    else:
        print("價格無變化，未寫入檔案")
    print("狀態：%s" % ("已更新" if changed else "無變化"))
    print("%-8s %-6s %9s %8s %8s %5s %8s %8s" % ("代碼", "名稱", "現價", "MA5", "MA20", "RSI", "支撐", "壓力"))
    for c in CODES:
        s = stats[c]
        print("%-8s %-6s %9.3f %8.2f %8.2f %5d %8.2f %8.2f"
              % (c, "", s["price"], s["ma5"], s["ma20"], s["rsi"], s["sup"], s["res"]))


if __name__ == "__main__":
    main()
