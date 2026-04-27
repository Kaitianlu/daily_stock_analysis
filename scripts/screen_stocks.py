#!/usr/bin/env python3
"""
A股选股筛选器 - 按 lxm-stock-plan.txt 规则匹配
方案：baostock 获取股票列表 + baostock 逐股K线验证全部4条规则

市场范围：沪市主板(600/601/603)、深市主板(000/001)、中小板(002)、创业板(300)
"""
import baostock as bs
import pandas as pd
import numpy as np
import socket
import time
import os
import sys
import json
import io
from contextlib import redirect_stdout

# 设置 socket 超时，避免 baostock 网络请求挂死
socket.setdefaulttimeout(15)

# 清除代理环境变量（避免 macOS 系统代理干扰）
for key in list(os.environ.keys()):
    if 'proxy' in key.lower():
        del os.environ[key]
os.environ['http_proxy'] = ''
os.environ['https_proxy'] = ''

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'reports')

def bs_login():
    """静默登录 baostock"""
    f = io.StringIO()
    with redirect_stdout(f):
        bs.login()

def bs_logout():
    """静默登出 baostock"""
    f = io.StringIO()
    with redirect_stdout(f):
        bs.logout()

# ── 市场范围 ──────────────────────────────────────────────
TARGET_PREFIXES = ('600', '601', '603', '000', '001', '002', '300')


# ── Phase 1: 获取股票列表 ──────────────────────────────────
def phase1_get_stock_list():
    """用 baostock 获取全市场股票列表，按代码前缀过滤，排除指数和ST"""
    print("=" * 60)
    print("Phase 1: 获取股票列表 (baostock)")
    print("=" * 60)

    bs_login()
    rs = bs.query_stock_basic()
    stocks = []
    while (rs.error_code == '0') & rs.next():
        row = rs.get_row_data()
        # row: [code, code_name, ipoDate, outDate, type, status]
        code = row[0]  # like 'sh.600519'
        name = row[1]
        stock_type = row[4]  # '1'=股票, '2'=指数
        status = row[5]

        # 只要正常上市的股票，排除指数
        if status != '1' or stock_type != '1':
            continue

        # 提取纯代码
        if '.' in code:
            pure_code = code.split('.')[1]
            market = code.split('.')[0]
        else:
            pure_code = code
            market = ''

        if not pure_code[:3] in TARGET_PREFIXES:
            continue

        # 排除ST
        if 'ST' in name or '退市' in name:
            continue

        stocks.append({'code': pure_code, 'name': name, 'market': market})

    bs_logout()
    print(f"  股票列表获取完成: {len(stocks)} 只")
    print(f"  沪市主板(600/601/603): {sum(1 for s in stocks if s['code'][:3] in ('600','601','603'))} 只")
    print(f"  深市主板(000/001): {sum(1 for s in stocks if s['code'][:3] in ('000','001'))} 只")
    print(f"  中小板(002): {sum(1 for s in stocks if s['code'][:3] == '002')} 只")
    print(f"  创业板(300): {sum(1 for s in stocks if s['code'][:3] == '300')} 只")
    return stocks


# ── Phase 2: K线获取 ─────────────────────────────────────
def fetch_daily_kline_bs(code, market):
    """用 baostock 拉取100交易日K线，返回标准化 DataFrame"""
    try:
        # baostock 需要带市场前缀的代码
        full_code = f"{market}.{code}"
        rs = bs.query_history_k_data_plus(
            full_code,
            "date,open,high,low,close,volume,amount,turn",
            start_date='2025-11-01',
            end_date='2026-04-25',
            frequency='d',
            adjustflag='1',  # 后复权
        )
        if rs.error_code != '0':
            return None

        rows = []
        while rs.next():
            rows.append(rs.get_row_data())

        if len(rows) < 95:
            return None

        df = pd.DataFrame(rows, columns=['日期', '开盘', '最高', '最低', '收盘', '成交量', '成交额', '换手率'])
        # 转换数值列
        for col in ['开盘', '最高', '最低', '收盘', '成交量', '成交额', '换手率']:
            df[col] = pd.to_numeric(df[col], errors='coerce')

        df = df.dropna(subset=['收盘'])
        if len(df) < 95:
            return None
        return df
    except Exception:
        return None


# ── 规则检查 ─────────────────────────────────────────────
def check_rule1(df):
    """规则1: 蓄势形态——收盘价温和振幅+小实体+阳多阴少（必要条件）"""
    if df is None or len(df) < 12:
        return False, {}

    recent = df.tail(9)
    closes = recent['收盘'].values
    opens = recent['开盘'].values

    max_close = closes.max()
    min_close = closes.min()
    avg_close = closes.mean()
    if avg_close <= 0:
        return False, {}

    # a) 收盘价振幅 ∈ [3%, 10%]
    close_amp = (max_close - min_close) / avg_close * 100
    if not (3.0 <= close_amp <= 10.0):
        return False, {'close_amp': round(close_amp, 2)}

    # b) 小实体：近9日实体中位数 < 2.5%（实体 = abs(收盘-开盘)/收盘 * 100）
    entities = []
    for o, c in zip(opens, closes):
        if c > 0:
            entities.append(abs(c - o) / c * 100)
    median_entity = np.median(entities) if entities else 0
    if median_entity >= 2.5:
        return False, {'close_amp': round(close_amp, 2), 'median_entity': round(float(median_entity), 2)}

    # c) 阳多阴少：阳线(收盘>开盘)数量 ≥ 阴线(收盘<开盘)数量
    bullish = (closes > opens).sum()
    bearish = (closes < opens).sum()
    if bullish < bearish:
        return False, {'close_amp': round(close_amp, 2), 'median_entity': round(float(median_entity), 2), 'bullish': int(bullish), 'bearish': int(bearish)}

    return True, {
        'close_amp': round(close_amp, 2),
        'median_entity': round(float(median_entity), 2),
        'bullish': int(bullish),
        'bearish': int(bearish),
        'max_close': round(float(max_close), 2),
        'min_close': round(float(min_close), 2),
        'avg_close': round(float(avg_close), 2),
    }


def check_rule2(df):
    """规则2: 近9个交易日内至少有一天成交量 >= 2x 前20日均量"""
    if df is None or len(df) < 30:
        return False, {}

    n = len(df)
    best_date, best_ratio, best_vol, best_avg = None, 0, 0, 0

    for i in range(max(0, n - 9), n):
        start = max(0, i - 20)
        prev = df.iloc[start:i]
        if len(prev) < 10:
            continue
        avg_vol = prev['成交量'].mean()
        if avg_vol <= 0:
            continue
        cur_vol = df.iloc[i]['成交量']
        ratio = cur_vol / avg_vol
        if ratio >= 2.0 and ratio > best_ratio:
            best_date = df.iloc[i].get('日期', '')
            best_ratio = ratio
            best_vol = cur_vol
            best_avg = avg_vol

    if best_date is not None:
        return True, {'date': str(best_date)[:10], 'ratio': round(best_ratio, 2), 'vol': int(best_vol), 'avg': int(best_avg)}
    return False, {}


def check_rule3(df):
    """规则3: 换手率保持5-6%以上（最近9日至少有7天 >= 5%）"""
    if df is None or len(df) < 12:
        return False, {}

    turn_col = '换手率'
    recent = df.tail(9)[turn_col]
    days_above_5 = (recent >= 5.0).sum()
    return days_above_5 >= 7, {'days_above_5': int(days_above_5), 'avg_turn': round(recent.mean(), 2)}


def check_rule4(df):
    """规则4: MA30 > MA60 > MA90 开始进入多头排列"""
    if df is None or len(df) < 95:
        return False, {}

    close = df['收盘'].values
    ma30 = pd.Series(close).rolling(30).mean().values
    ma60 = pd.Series(close).rolling(60).mean().values
    ma90 = pd.Series(close).rolling(90).mean().values

    m30, m60, m90 = ma30[-1], ma60[-1], ma90[-1]
    if np.isnan(m30) or np.isnan(m60) or np.isnan(m90):
        return False, {}

    if m30 > m60 > m90:
        was_aligned = 0
        for offset in range(1, 6):
            if len(ma30) > offset:
                p30, p60, p90 = ma30[-1-offset], ma60[-1-offset], ma90[-1-offset]
                if not (np.isnan(p30) or np.isnan(p60) or np.isnan(p90)):
                    if p30 > p60 > p90:
                        was_aligned += 1
        recently = was_aligned <= 2
        return True, {'MA30': round(m30,2), 'MA60': round(m60,2), 'MA90': round(m90,2), 'new': recently}
    return False, {}


def phase2_screen(stocks):
    """逐股拉取K线，验证规则1-4（R1为必要条件，R2/R3/R4为优先级排序）"""
    print("\n" + "=" * 60, flush=True)
    print("Phase 2: K线深筛 (baostock)", flush=True)
    print("=" * 60, flush=True)

    results = []
    total = len(stocks)
    start_time = time.time()

    for i, s in enumerate(stocks):
        code = s['code']
        name = s['name']
        market = s['market']

        elapsed = time.time() - start_time
        eta = (elapsed / max(i, 1)) * (total - i) if i > 0 else 0
        prefix = f"[{i+1:4d}/{total}] {code} {name}"

        bs_login()
        df = fetch_daily_kline_bs(code, market)
        bs_logout()

        if df is None:
            if i % 100 == 0:
                eta_str = f"ETA:{eta:.0f}s" if i > 0 else ""
                print(f"{prefix:<48} - 无K线 {eta_str}", flush=True)
            continue

        # R1 作为硬性门控：不通过直接跳过，不打印
        r1_ok, r1_info = check_rule1(df)
        if not r1_ok:
            time.sleep(0.05)
            continue

        # R1 通过后检查 R2/R3/R4
        r2_ok, r2_info = check_rule2(df)
        r3_ok, r3_info = check_rule3(df)
        r4_ok, r4_info = check_rule4(df)

        matched = 1 + sum([bool(r2_ok), bool(r3_ok), bool(r4_ok)])  # R1 已通过，总分 1-4

        tags = [f"R1振幅:{r1_info['close_amp']}%"]
        if r2_ok: tags.append(f"R2量比:{r2_info['ratio']}x")
        if r3_ok: tags.append(f"R3换手:{r3_info['avg_turn']}%")
        if r4_ok: tags.append(f"R4多头{'🆕' if r4_info.get('new') else ''}")
        status = " | ".join(tags)

        level = "🔥" if matched >= 4 else "⭐" if matched >= 3 else "•" if matched >= 2 else "·"
        print(f"{prefix:<48} {level} {matched}/4 [{status}]", flush=True)

        results.append({
            'code': code, 'name': name,
            'r1_ok': bool(r1_ok), 'r1_info': r1_info,
            'r2_ok': bool(r2_ok), 'r2_info': r2_info,
            'r3_ok': bool(r3_ok), 'r3_info': r3_info,
            'r4_ok': bool(r4_ok), 'r4_info': r4_info,
            'matched': int(matched),
        })

        time.sleep(0.05)

    # 按匹配数降序排列
    results.sort(key=lambda r: r['matched'], reverse=True)

    matched_counts = {}
    for r in results:
        matched_counts[r['matched']] = matched_counts.get(r['matched'], 0) + 1
    summary = " | ".join([f"{m}/4: {c}只" for m, c in sorted(matched_counts.items(), reverse=True)])
    print(f"\n  ✅ Phase 2 完成: {len(results)} 只通过R1 ({summary})", flush=True)
    return results


# ── 输出 ─────────────────────────────────────────────────
def print_results(results):
    print("\n" + "=" * 60)
    print("📊 最终筛选结果")
    print("=" * 60)

    if not results:
        print("\n  没有找到符合条件的股票。")
        return

    # results 已按 matched 降序排列
    full = [r for r in results if r['matched'] >= 4]
    three = [r for r in results if r['matched'] == 3]
    two = [r for r in results if r['matched'] == 2]
    one = [r for r in results if r['matched'] == 1]

    print(f"\n  🔥 4/4: {len(full)}只 | ⭐ 3/4: {len(three)}只 | • 2/4: {len(two)}只 | · 1/4(R1): {len(one)}只\n")

    for group in [full, three, two, one]:
        for r in group:
            print_stock_detail(r)


def print_stock_detail(r):
    print(f"\n{'='*55}")
    print(f"  {r['code']} {r['name']}  [{r['matched']}/4]")
    print(f"{'='*55}")

    i = r['r1_info']
    print(f"  ✅ 规则1 [蓄势形态]: 收盘振幅{i['close_amp']}% 实体中位数{i['median_entity']}% 阳{i['bullish']}阴{i['bearish']} (收盘区间{i['min_close']}-{i['max_close']})")

    if r['r2_ok']:
        i = r['r2_info']
        print(f"  ✅ 规则2 [底部倍量]: {i['date']} 量比 {i['ratio']}x (当日{i['vol']} vs 20日均{i['avg']})")
    else:
        print(f"  ❌ 规则2 [底部倍量]: 不满足")

    if r['r3_ok']:
        i = r['r3_info']
        print(f"  ✅ 规则3 [换手率≥5%]: 近9日{i['days_above_5']}天≥5%, 均值{i['avg_turn']}%")
    else:
        print(f"  ❌ 规则3 [换手率≥5%]: 不满足")

    if r['r4_ok']:
        i = r['r4_info']
        flag = "🆕 刚形成" if i['new'] else "已持续"
        print(f"  ✅ 规则4 [MA多头排列]: MA30({i['MA30']}) > MA60({i['MA60']}) > MA90({i['MA90']}) [{flag}]")
    else:
        print(f"  ❌ 规则4 [MA多头排列]: 不满足")


# ── 入口 ─────────────────────────────────────────────────
def main():
    t0 = time.time()

    # Phase 1
    stocks = phase1_get_stock_list()
    if not stocks:
        print("❌ 股票列表为空")
        return

    # Phase 2
    results = phase2_screen(stocks)

    # Save to JSON
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    json_path = os.path.join(OUTPUT_DIR, 'screen_results.json')
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n💾 结果已保存: {json_path}", flush=True)

    # Output
    print_results(results)

    print(f"\n⏱  总耗时: {time.time() - t0:.0f} 秒")
    print(f"📁 全市场 {len(stocks)} 只 → 匹配 {len(results)} 只")


if __name__ == '__main__':
    main()
