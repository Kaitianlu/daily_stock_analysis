# A股选股筛选器

按 `lxm-stock-plan.txt` 中的4条技术规则，对沪市主板(600/601/603)、深市主板(000/001)、中小板(002)、创业板(300)进行全市场筛选。

## Usage

```text
/screen-stocks
```

## 选股规则（来自 lxm-stock-plan.txt）

| 规则 | 描述 | 判定条件 |
|------|------|----------|
| R1 蓄势形态 | 必要条件：收盘价温和振幅+小实体+阳多阴少 | a) 收盘价振幅∈[3%, 10%] b) 近9日实体中位数<2.5% c) 阳线≥阴线 |
| R2 底部倍量 | 近期9个交易日内有一天有倍量成交 | 近9日中任一天成交量 ≥ 2×前20日均量 |
| R3 换手率 | 换手率保持在5-6%以上 | 近9日至少7天换手率 ≥ 5% |
| R4 多头排列 | MA30>MA60>MA90 开始进入多头排列 | MA30>MA60>MA90，且回溯5天已对齐≤2天(刚形成) |

- R1 为**必要条件**，不通过则不进入后续筛选
- R2/R3/R4 为**优先级排序**，通过R1后计算匹配数(1-4)并降序排列

## Instructions

### Step 1: 运行全市场筛选

```bash
.venv/bin/python scripts/screen_stocks.py
```

- 约需35分钟（3800只股票，每只独立baostock登录/登出）
- R1 为必要条件，通过R1的股票全部记录并按 R2/R3/R4 匹配数降序排列
- 实时打印所有通过R1的股票
- 结果保存至 `reports/screen_results.json`

### Step 2: 查看结果摘要

```bash
.venv/bin/python -c "
import json
with open('reports/screen_results.json') as f:
    results = json.load(f)
full = [r for r in results if int(r['matched']) >= 4]
three = [r for r in results if int(r['matched']) == 3]
two = [r for r in results if int(r['matched']) == 2]
one = [r for r in results if int(r['matched']) == 1]
print(f'4/4: {len(full)} | 3/4: {len(three)} | 2/4: {len(two)} | 1/4: {len(one)}')
"
```

### Step 3: 优先关注高匹配股票

所有结果已通过R1必要条件，按匹配数降序排列（4/4 > 3/4 > 2/4 > 1/4）。生成格式化报告：

```bash
.venv/bin/python scripts/screen_stocks.py --report
```

或手动处理 `reports/screen_results.json`，按优先级排序输出。

### Step 4: 对重点标的执行AI深度分析

```bash
.venv/bin/python main.py --stocks 600926 --force-run
```

注意：非交易日需加 `--force-run`。

### Step 5: 查看新闻情报

分析完成后，新闻情报存储在 SQLite 数据库中，可直接查询：

```bash
.venv/bin/python -c "
import sqlite3
conn = sqlite3.connect('data/stock_analysis.db')
cursor = conn.cursor()
cursor.execute(\"\"\"
    SELECT code, dimension, provider, title, snippet, source, published_date
    FROM news_intel WHERE code = 'STOCK_CODE'
    ORDER BY fetched_at DESC
\"\"\")
for row in cursor.fetchall():
    print(f'[{row[1]}] {row[3]} | {row[2]} | {row[6]}')
    print(f'  {row[4][:200] if row[4] else \"N/A\"}')
    print()
conn.close()
"
```

## 结果解读

### 匹配含义

- **4/4 全匹配**：极少出现，蓄势+倍量+高换手+多头排列，最强信号
- **3/4**：R1+任意两条附加规则，重点关注
- **2/4**：R1+一条附加规则，可观察
- **1/4 (仅R1)**：仅通过蓄势形态必要条件，待附加信号确认
- R1 不通过则不出现在结果中

### 板块特征

银行股等大市值蓝筹天然换手率低（<1%），通常不满足R3，需结合成交量倍量判断活跃度。

### 数据源说明

- 股票列表：baostock (`bs.query_stock_basic()`)
- K线数据：baostock (`bs.query_history_k_data_plus()`，后复权，近100个交易日)
- 实时行情（AI分析时）：腾讯财经 (量比、换手率)
- 新闻搜索：Anspire / Bocha / SerpAPI 轮询分发
