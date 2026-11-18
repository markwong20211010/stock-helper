#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
股票買入時機小助手 —— 行情自動更新（GitHub Actions 版） v3

新架構（重點：機械人只寫一個細檔案，唔再改你嘅 index.html）
------------------------------------------------------------------
  stocks.json  ← 你自己維護的清單（編號／名稱／目標價／分組／文案）→ 機械人只讀不寫
  data.json    ← 機械人輸出（現價、MA5/MA20、RSI、支撐壓力、更新時間）→ 只此一個檔案會被 commit
  index.html   ← 頁面開機時 fetch data.json，抓唔到就沿用內嵌 DATA（離線仍可用）

好處：你改 index.html / 我改頁面，都唔會再同機械人嘅 commit 撞出「合併衝突」。

用法：
    python3 scripts/update_stock_data.py                # 預設：讀 stocks.json → 寫 data.json
    python3 scripts/update_stock_data.py --embed        # 額外把 DATA 重新嵌入 index.html（可選，一般唔需要）

不會改動任何操作邏輯、界面、分組卡片、密碼鎖。
目標買入價優先序：stocks.json 的 target → 上次 data.json 的 target → 首次自動「現價 −3%」（之後固定不再漂移）
"""
import sys, os, json, re, time, urllib.request, datetime, argparse

HKT = datetime.timezone(datetime.timedelta(hours=8))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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


def read_json(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print("⚠ %s 讀唔到（%s），當作空白處理" % (os.path.basename(path), e))
        return {}


def enrich(stocks, quotes, prev):
    """把行情與指標填入 stocks；target 由 stocks.json > 上次 data.json > 現價 −3% 決定。"""
    for s in stocks:
        s["code"] = str(s["code"]).zfill(5)
        code = s["code"]
        old = prev.get(code) or {}
        q = quotes.get(code)
        if not q:
            # 抓唔到就沿用上一次的值，唔好把數字清空
            for k in ("price", "ma5", "ma20", "rsi", "sup", "res"):
                if s.get(k) is None and old.get(k) is not None:
                    s[k] = old[k]
            if not s.get("name"):
                s["name"] = old.get("name") or code
            if s.get("target") in (None, "", 0):
                s["target"] = old.get("target")
            print("⚠ 抓不到 %s 行情，沿用舊值" % code)
            continue

        closes, highs, lows = fetch_kline(code)
        cur = q["price"]
        if not closes or abs(closes[-1] - cur) > 0.01:
            closes += [cur]
            highs += [q["high"]]
            lows += [q["low"]]

        if not s.get("name"):
            s["name"] = old.get("name") or q["name"]

        # 目標價：明確設定 > 上次落地值 > 首次自動 −3%
        tgt = s.get("target")
        if tgt in (None, "", 0):
            tgt = old.get("target")
        if tgt in (None, "", 0):
            tgt = round(cur * 0.97, 2)
            s["autotarget"] = True
            s["note"] = (s.get("note") or "").rstrip("。") + "（自動目標：現價 −3%，可自行改 target）。"
        s["target"] = tgt

        s.setdefault("y", old.get("y", "—"))
        s.setdefault("grp", old.get("grp", "自選"))
        s.setdefault("note", old.get("note", "自訂加入"))
        s.setdefault("news", old.get("news", ""))
        s["price"] = cur
        s["ma5"] = sum(closes[-5:]) / 5
        s["ma20"] = sum(closes[-20:]) / 20
        s["rsi"] = rsi14(closes)
        s["sup"] = min(lows[-20:])
        s["res"] = max(highs[-20:])
    return stocks


def to_records(stocks):
    recs = []
    for s in stocks:
        if s.get("price") is None:
            continue
        recs.append({
            "code": s["code"], "name": s["name"],
            "price": round(float(s["price"]), 3),
            "target": (round(float(s["target"]), 3) if s.get("target") not in (None, "") else None),
            "y": s.get("y", "—"), "grp": s.get("grp", "自選"),
            "note": s.get("note", ""), "news": s.get("news", ""),
            "ma5": round(float(s["ma5"]), 3), "ma20": round(float(s["ma20"]), 3),
            "rsi": int(s["rsi"]),
            "sup": round(float(s["sup"]), 3), "res": round(float(s["res"]), 3),
        })
    return recs


def embed_into_index(index_path, recs):
    """可選：把最新 DATA 嵌入 index.html（只作離線兜底）。定位用大括號配對，唔靠脆弱嘅正則。"""
    if not os.path.exists(index_path):
        print("ℹ 冇 index.html，略過嵌入")
        return False
    html = open(index_path, encoding="utf-8").read()
    if "var DATA" not in html:
        print("ℹ index.html 冇 var DATA（新版頁面靠 data.json），略過嵌入")
        return False
    payload = "var DATA = " + json.dumps(
        {r["code"]: {k: r[k] for k in ("name", "price", "target", "y", "grp", "note",
                                       "ma5", "ma20", "rsi", "sup", "res", "news")}
         for r in recs}, ensure_ascii=False, separators=(", ", ": ")) + ";"

    start = html.find("var DATA")
    brace = html.find("{", start)
    if brace < 0:
        return False
    depth, i, in_str, esc = 0, brace, False, False
    while i < len(html):
        ch = html[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    break
        i += 1
    if depth != 0:
        print("⚠ index.html 的 DATA 大括號唔平衡，略過嵌入（唔會弄壞頁面）")
        return False
    end = i + 1
    new_html = html[:start] + payload + html[end:]
    if new_html != html:
        open(index_path, "w", encoding="utf-8").write(new_html)
        return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("index", nargs="?", default=os.path.join(ROOT, "index.html"))
    ap.add_argument("stocks", nargs="?", default=os.path.join(ROOT, "stocks.json"))
    ap.add_argument("--embed", action="store_true", help="另外把 DATA 嵌入 index.html（預設不做）")
    ap.add_argument("--out", default=None, help="data.json 輸出路徑（預設與 stocks.json 同目錄）")
    a = ap.parse_args()

    stocks_path = a.stocks
    out_path = a.out or os.path.join(os.path.dirname(os.path.abspath(stocks_path)), "data.json")

    doc = read_json(stocks_path)
    stocks = doc.get("stocks") or []
    if not stocks:
        # 冇清單就由 data.json 或 index.html 的 DATA 反向建立
        prev = read_json(out_path)
        stocks = prev.get("stocks") or []
        if not stocks:
            print("✗ stocks.json 冇內容，亦冇 data.json 可用，停止。")
            sys.exit(1)

    prev_recs = {r["code"]: r for r in (read_json(out_path).get("stocks") or []) if r.get("code")}

    codes = [str(s["code"]).zfill(5) for s in stocks]
    quotes = fetch_quotes(codes)
    stocks = enrich(stocks, quotes, prev_recs)

    recs = to_records(stocks)
    now = datetime.datetime.now(HKT)
    out = {"updated": now.strftime("%Y-%m-%d %H:%M HKT"), "stocks": recs}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    if a.embed:
        embed_into_index(a.index, recs)

    print("data.json 已更新：%d 隻 · %s" % (len(recs), out["updated"]))
    for s in recs:
        print("%s %-8s 現價 %8.3f  目標 %s  MA20 %7.2f  RSI %3d"
              % (s["code"], s["name"], s["price"], s["target"], s["ma20"], s["rsi"]))
    print("::notice::完成，共 %d 隻股票，資料寫入 data.json" % len(recs))


if __name__ == "__main__":
    main()
