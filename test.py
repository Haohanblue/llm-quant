import backtrader as bt
import sqlalchemy
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager

# 设置中文字体以避免乱码
plt.rcParams['font.family'] = 'Simhei'
plt.rcParams['axes.unicode_minus'] = False  # 解决无法显示负号的问题

# 定义一个选股轮动策略
class RotationStrategy(bt.Strategy):
    params = (
        ('rotation_days', 30),  # 轮动天数
        ('num_stocks', 5),      # 每次轮动买入的股票数量
    )

    def __init__(self, df):
        self.counter = 0
        self.current_stocks = []
        self.portfolio_values = []  # 用于存储投资组合价值
        self.dates = []  # 用于存储交易日期
        self.df = df  # 预先加载的因子数据

    def next(self):
        self.counter += 1
        self.portfolio_values.append(self.broker.getvalue())  # 记录每一步的投资组合价值
        current_date = self.data.datetime.date(0)
        self.dates.append(current_date)  # 记录交易日期
        if self.counter % self.params.rotation_days == 0:
            self.rebalance_portfolio()

    def rebalance_portfolio(self):
        # 清仓之前的股票
        for stock in self.current_stocks:
            self.close_position(stock)
        
        # 计算所有股票的评分
        scores = self.calculate_all_scores(self.df)
        
        # 选择评分最高的股票
        self.current_stocks = [score[0] for score in scores[:self.params.num_stocks]]
        
        # 输出选中股票的评分
        self.print_selected_stocks(scores)
        
        # 买入新的股票
        self.buy_new_stocks()

    def close_position(self, stock):
        self.close(data=stock)
        sell_price = stock.close[0]
        sell_size = self.getposition(stock).size
        sell_value = sell_price * sell_size
        cost_basis = self.getposition(stock).price * sell_size
        profit = sell_value - cost_basis
        print(f"{self.data.datetime.date(0)} 卖出股票: {stock._name}, 卖出数量: {sell_size}, "
              f"每股价格: {sell_price:.2f}, 总金额: {sell_value:.2f}, 成本: {cost_basis:.2f}, "
              f"本次交易收益: {profit:.2f}")

    def calculate_all_scores(self, df):
        scores = []
        for data in self.datas:
            ts_code = data._name
            row = df[df['ts_code'] == ts_code]
            if not row.empty:
                row = row.iloc[0].copy()  # 复制行以便修改
                row['close'] = data.close[0]  # 添加日线数据
                score = self.calculate_score(row)
                scores.append((data, score))
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores

    def print_selected_stocks(self, scores):
        print("选股评分最高的前5支股票:")
        for stock, score in scores[:self.params.num_stocks]:
            print(f"股票: {stock._name}, 评分: {score:.2f}")

    def buy_new_stocks(self):
        total_cash = self.broker.getvalue()  # 获取总资产
        cash_per_stock = total_cash / 10  # 每只股票的最大投资金额为总资产的十分之一
        for stock in self.current_stocks:
            price = stock.close[0]
            size = int(cash_per_stock // (price * 100)) * 100  # 计算可以买入的手数，确保是100的倍数
            if size > 0:
                self.buy(data=stock, size=size)
                current_balance = self.broker.getcash()  # 获取当前余额
                print(f"{self.data.datetime.date(0)} 买入股票: {stock._name}, 成交价格: {price:.2f}, "
                      f"买入数量: {size}")

    def calculate_score(self, row):
        # 新策略思路:
        # 1. 增加对盈利能力的重视，使用市盈率（PE）作为因子。
        # 2. 增加对成长性的考量，使用市销率（PS）作为因子。
        # 3. 继续使用市净率（PB）作为估值因子，但调整权重。
        # 4. 使用动量因子（如RSI）来捕捉市场趋势。
        # 5. 使用波动率因子（如Bollinger Bands）来评估市场风险。
        # 6. 使用成交量因子（如Volume Ratio）来评估市场活跃度。

        # 定义因子和计算因子的评分
        factors = {
            'pe': lambda x: -x if x > 0 else 0,
            'pb': lambda x: -x if x > 0 else 0,
            'ps': lambda x: -x if x > 0 else 0,  # PS因子，越低越好
            'rsi': lambda rsi6, rsi12, rsi24: (rsi6 + rsi12 + rsi24) / 3,
            'bollinger': lambda x, upper, lower: -1 if x > upper else (1 if x < lower else 0),
            'volume_ratio': lambda x: x
        }

        # 计算各因子的评分
        pe_score = factors['pe'](row['pe'])
        pb_score = factors['pb'](row['pb'])
        ps_score = factors['ps'](row['ps'])
        rsi_score = factors['rsi'](row['rsi_bfq_6'], row['rsi_bfq_12'], row['rsi_bfq_24'])
        bollinger_score = factors['bollinger'](row['close'], row['boll_upper_bfq'], row['boll_lower_bfq'])
        volume_ratio_score = factors['volume_ratio'](row['volume_ratio'])

        # 综合评分公式
        # 重新分配权重以反映新的策略重点
        score = (0.2 * pe_score + 0.15 * pb_score + 0.15 * ps_score +
                 0.2 * rsi_score + 0.15 * bollinger_score + 0.15 * volume_ratio_score)

        return score

    def get_factors(self):
        # 返回需要查询的因子名称列表
        return ['pe', 'pb', 'ps', 'rsi_bfq_6', 'rsi_bfq_12', 'rsi_bfq_24', 
                'boll_upper_bfq', 'boll_lower_bfq', 'volume_ratio']

def get_trade_dates(start_date, end_date):
    # 使用SQLAlchemy连接到MySQL数据库
    engine = sqlalchemy.create_engine(
        'mysql+pymysql://quantuser:Quantuser233.@bj-cynosdbmysql-grp-3upmvv08.sql.tencentcdb.com:27017/stock'
    )
    
    # 查询交易日历表以获取交易日期
    query = f"""
    SELECT cal_date
    FROM trade_cal_sh
    WHERE is_open = 1
    AND cal_date BETWEEN '{start_date}' AND '{end_date}'
    """
    df = pd.read_sql(query, engine, parse_dates=['cal_date'])
    return df['cal_date'].dt.strftime('%Y-%m-%d').tolist()

def get_technical_factors(stock_pool, trade_dates, factors):
    """
    从数据库中获取指定股票池、日期范围和技术因子的技术因子数据。

    :param stock_pool: list, 股票代码列表
    :param trade_dates: list, 需要查询的交易日期列表
    :param factors: list, 需要查询的技术因子名称列表
    :return: pandas.DataFrame, 包含查询结果的数据框
    """
    # 使用SQLAlchemy连接到MySQL数据库
    engine = sqlalchemy.create_engine(
        'mysql+pymysql://quantuser:Quantuser233.@bj-cynosdbmysql-grp-3upmvv08.sql.tencentcdb.com:27017/stock'
    )
    
    # 构建SQL查询语句
    query = f"""
    SELECT ts_code, trade_date, {', '.join(factors)}
    FROM stk_factor_pro
    WHERE ts_code IN ({','.join([f"'{ts}'" for ts in stock_pool])})
    AND trade_date IN ({','.join([f"'{date}'" for date in trade_dates])})
    """
    
    # 执行查询并返回结果
    df = pd.read_sql(query, engine, parse_dates=['trade_date'])
    print("股票技术指标数据",df)
    return df

def get_data_from_mysql(stock_pool, trade_dates):
    # 使用SQLAlchemy连接到MySQL数据库
    engine = sqlalchemy.create_engine(
        'mysql+pymysql://quantuser:Quantuser233.@bj-cynosdbmysql-grp-3upmvv08.sql.tencentcdb.com:27017/stock'
    )
    
    # 查询daily表中的数据，指定日期范围和股票代码
    query = f"""
    SELECT ts_code, trade_date, open, high, low, close, vol as volume
    FROM daily 
    WHERE ts_code IN ({','.join([f"'{ts}'" for ts in stock_pool])}) 
    AND trade_date IN ({','.join([f"'{date}'" for date in trade_dates])})
    """
    df = pd.read_sql(query, engine, parse_dates=['trade_date'])
    print("日线数据",df)
    # 将数据分配给不同的股票
    data_dict = {}
    for ts_code in stock_pool:
        stock_df = df[df['ts_code'] == ts_code].set_index('trade_date')
        data_dict[ts_code] = bt.feeds.PandasData(dataname=stock_df, name=ts_code)
    
    return data_dict

def get_benchmark_data(trade_dates):
    # 使用SQLAlchemy连接到MySQL数据库
    engine = sqlalchemy.create_engine(
        'mysql+pymysql://quantuser:Quantuser233.@bj-cynosdbmysql-grp-3upmvv08.sql.tencentcdb.com:27017/stock'
    )
    
    # 查询index_daily_basic表中的上证指数数据
    query = f"""
    SELECT ts_code, trade_date, open, close
    FROM idx_factor_pro
    WHERE ts_code = '000001.SH'
    AND trade_date IN ({','.join([f"'{date}'" for date in trade_dates])})
    """
    df = pd.read_sql(query, engine, parse_dates=['trade_date'])
    print("上证指数数据",df)
    # 计算累计收益率
    df['cumulative_return'] = (df['close'] / df['open'].iloc[0] - 1) * 100
    return df

def run_backtest(stock_pool, start_date, end_date, rotation_days=30, num_stocks=5):
    # 创建Cerebro引擎实例
    cerebro = bt.Cerebro()

    # 计算需要查询的交易日期
    all_trade_dates = get_trade_dates(start_date, end_date)

    # 从MySQL数据库加载因子数据
    factors = RotationStrategy.get_factors(RotationStrategy)
    df = get_technical_factors(stock_pool, all_trade_dates, factors)

    # 添加策略并传递因子数据
    cerebro.addstrategy(RotationStrategy, df=df, rotation_days=rotation_days, num_stocks=num_stocks)

    # 从MySQL数据库加载数据
    data_dict = get_data_from_mysql(stock_pool, all_trade_dates)
    for ts_code, data in data_dict.items():
        cerebro.adddata(data)
    
    # 设置初始现金
    cerebro.broker.setcash(1000000)

    # 打印初始投资组合价值
    initial_value = cerebro.broker.getvalue()
    print('初始投资组合价值: %.2f' % initial_value)
    
    # 运行回测
    cerebro.run()
    
    # 打印最终投资组合价值
    final_value = cerebro.broker.getvalue()
    print('最终投资组合价值: %.2f' % final_value)

    # 计算并打印回测结果指标
    pnl = final_value - initial_value
    print('净利润: %.2f' % pnl)
    print('收益率: %.2f%%' % ((pnl / initial_value) * 100))

    # 计算最大回撤
    strategy = cerebro.runstrats[0][0]
    portfolio_values = strategy.portfolio_values  # 从策略中获取投资组合价值历史
    drawdowns = [(1 - pv / max(portfolio_values[:i+1])) for i, pv in enumerate(portfolio_values)]
    max_drawdown = max(drawdowns)
    print('最大回撤: %.2f%%' % (max_drawdown * 100))

    # 计算夏普比率
    returns = np.diff(portfolio_values) / portfolio_values[:-1]
    sharpe_ratio = np.mean(returns) / np.std(returns) * np.sqrt(252)  # 年化
    print('夏普比率: %.2f' % sharpe_ratio)

    # 计算收益率曲线
    returns_cumulative = (np.array(portfolio_values) / initial_value - 1) * 100

    # 获取上证指数数据作为基准
    benchmark_data = get_benchmark_data(all_trade_dates)
    benchmark_returns = benchmark_data['cumulative_return'].tolist()

    # 绘制收益率曲线
    plt.plot(strategy.dates, returns_cumulative, label='策略收益率')
    plt.plot(benchmark_data['trade_date'], benchmark_returns, label='上证指数收益率', linestyle='--')
    plt.title('收益率曲线')
    plt.xlabel('日期')
    plt.ylabel('收益率 (%)')
    plt.legend()
    plt.show()

if __name__ == '__main__':
    # 示例用法:
    # 使用上证五十的成分股进行测试
    stock_pool = [
        '600000.SH', '600016.SH', '600019.SH', '600028.SH', '600029.SH',
        '600030.SH', '600036.SH', '600048.SH', '600050.SH', '600104.SH',
        '600111.SH', '600196.SH', '600276.SH', '600309.SH', '600340.SH',
        '600519.SH', '600547.SH', '600570.SH', '600585.SH', '600588.SH',
        '600690.SH', '600703.SH', '600745.SH', '600837.SH', '600887.SH',
        '600893.SH', '600900.SH', '600919.SH', '600958.SH', '600999.SH',
        '601012.SH', '601066.SH', '601088.SH', '601138.SH', '601166.SH',
        '601169.SH', '601186.SH', '601211.SH', '601229.SH', '601288.SH',
        '601318.SH', '601319.SH', '601336.SH', '601390.SH', '601398.SH',
        '601601.SH', '601628.SH', '601668.SH', '601688.SH', '601818.SH'
    ]
    run_backtest(stock_pool, '2019-01-01', '2023-12-31', rotation_days=20, num_stocks=10)

# 所需依赖:
# - backtrader: 用于回测交易策略的Python库
# - sqlalchemy: 用于连接MySQL数据库的库
# - pandas: 用于数据处理的库
# - numpy: 用于数值计算的库