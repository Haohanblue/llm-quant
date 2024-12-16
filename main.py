import backtrader as bt
import sqlalchemy
import pandas as pd
from pyecharts.charts import Line, Page
from pyecharts import options as opts
import numpy as np
import time
from datetime import datetime, timedelta
import logging
import json  # 引入json模块
from strategy import get_factor_score,get_factor_list
# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


class CustomPandasData(bt.feeds.PandasData):
    """
    扩展Backtrader的PandasData，添加is_limit_up, is_limit_down, is_suspended字段。
    """
    lines = ('is_limit_up', 'is_limit_down', 'is_suspended',)
    params = (
        ('is_limit_up', -1),
        ('is_limit_down', -1),
        ('is_suspended', -1),
    )


class DataHandler:
    """
    数据获取与交互模块
    负责从数据库中查询交易日期、股票日线数据、技术指标数据和基准数据。
    """
    def __init__(self, db_url):
        self.engine = sqlalchemy.create_engine(db_url)

    def get_stock_code_list(self):
        query = f"""
                SELECT DISTINCT ts_code
                FROM stock_basic
        """
        try:
            df = pd.read_sql(query, self.engine)
            code_list = df["ts_code"].tolist()
            return code_list
        except Exception as e:
            logging.error(f"无法获取交易日期 - {e}")
            return []

    def get_index_code_list(self, index_code):
        query = f"""
                SELECT DISTINCT con_code
                FROM index_weight
                WHERE index_code = '{index_code}'
        """
        try:
            df = pd.read_sql(query, self.engine)
            code_list = df["con_code"].tolist()
            return code_list
        except Exception as e:
            logging.error(f"无法获取交易日期 - {e}")
            return []
        
    def get_trade_dates(self, start_date, end_date):
        # 查询指定日期范围内所有交易日期的数据
        query = f"""
                SELECT cal_date
                FROM trade_cal_sh
                WHERE is_open = 1
                AND cal_date BETWEEN '{start_date}' AND '{end_date}'
                """
        try:
            df = pd.read_sql(query, self.engine, parse_dates=['cal_date'])
        except Exception as e:
            logging.error(f"无法获取交易日期 - {e}")
            return []

        if df.empty:
            logging.warning("未找到交易日期。")
            return []

        # 将交易日期按升序排列
        df = df.sort_values('cal_date')

        # 转换为日期类型并返回
        trade_dates = df['cal_date'].dt.strftime('%Y-%m-%d').tolist()
        logging.info(f"获取交易日期的前5个: {trade_dates[:5]}...（共 {len(trade_dates)} 天）")
        return trade_dates

    def get_technical_factors(self, stock_pool, trade_dates, factors):
        """
        获取技术因子数据。
        """
        if not stock_pool or not trade_dates or not factors:
            logging.warning("股票池、交易日期或因子列表为空。")
            return pd.DataFrame()

        # 使用参数化查询以提高安全性和效率
        stock_pool_str = ",".join([f"'{ts}'" for ts in stock_pool])
        trade_dates_str = ",".join([f"'{date}'" for date in trade_dates])
        query = f"""
        SELECT ts_code, trade_date, {', '.join(factors)}
        FROM stk_factor_pro
        WHERE ts_code IN ({stock_pool_str})
        AND trade_date IN ({trade_dates_str})
        """

        try:
            df = pd.read_sql(query, self.engine, parse_dates=['trade_date'])
        except Exception as e:
            logging.error(f"无法获取技术因子数据 - {e}")
            return pd.DataFrame()

        if df.empty:
            logging.warning("未找到技术因子数据。")
        else:
            logging.info("股票技术指标数据获取成功。")
            logging.debug(df.head())
        return df

    def get_data_from_mysql(self, stock_pool, trade_dates):
        """
        获取股票日线数据，并计算is_limit_up, is_limit_down, is_suspended。
        """
        if not stock_pool or not trade_dates:
            logging.warning("股票池或交易日期为空。")
            return {}

        # 获取所有交易日期为DataFrame
        trade_dates_df = pd.DataFrame({'trade_date': pd.to_datetime(trade_dates)})

        # 使用参数化查询以提高安全性和效率
        stock_pool_str = ",".join([f"'{ts}'" for ts in stock_pool])
        query = f"""
        SELECT ts_code, trade_date, open, high, low, close, vol as volume
        FROM daily 
        WHERE ts_code IN ({stock_pool_str})
        AND trade_date BETWEEN '{trade_dates[0]}' AND '{trade_dates[-1]}'
        """

        try:
            df = pd.read_sql(query, self.engine, parse_dates=['trade_date'])
        except Exception as e:
            logging.error(f"无法获取股票日线数据 - {e}")
            return {}

        if df.empty:
            logging.warning("没有找到股票日线数据。")
            return {}

        data_dict = {}
        for ts_code, group in df.groupby('ts_code'):
            # 合并所有交易日期，填充缺失数据
            stock_df = trade_dates_df.merge(group, on='trade_date', how='left')
            stock_df.sort_values('trade_date', inplace=True)
            stock_df.reset_index(drop=True, inplace=True)

            # 计算前一日收盘价
            stock_df['prev_close'] = stock_df['close'].shift(1)

            # 计算涨跌停
            stock_df['is_limit_up'] = ((stock_df['open'] >= stock_df['prev_close'] * 1.095) & (stock_df['open'] < stock_df['prev_close'] * 1.105))
            stock_df['is_limit_down'] = ((stock_df['open'] <= stock_df['prev_close'] * 0.905) & (stock_df['open'] > stock_df['prev_close'] * 0.895))

            # 处理第一天的前一日收盘价为NaN
            stock_df['is_limit_up'] = stock_df['is_limit_up'].fillna(False)
            stock_df['is_limit_down'] = stock_df['is_limit_down'].fillna(False)

            # 处理停牌：如果某天的数据缺失（open为NaN），则认为该天停牌
            stock_df['is_suspended'] = stock_df['open'].isna()

            # 填充停牌日的价格数据为0或其他适当的值
            stock_df[['open', 'high', 'low', 'close', 'volume']] = stock_df[['open', 'high', 'low', 'close', 'volume']].fillna(0)

            # 设置日期为索引
            stock_df.set_index('trade_date', inplace=True)

            # 创建Backtrader的数据源
            data = CustomPandasData(
                dataname=stock_df,
                name=ts_code,
                fromdate=pd.to_datetime(stock_df.index.min()),
                todate=pd.to_datetime(stock_df.index.max()),
                is_limit_up='is_limit_up',
                is_limit_down='is_limit_down',
                is_suspended='is_suspended'
            )
            data_dict[ts_code] = data

        return data_dict

    def get_benchmark_data(self, trade_dates):
        """
        获取基准指数数据（如上证指数）。
        """
        if not trade_dates:
            logging.warning("交易日期为空。")
            return pd.DataFrame()

        dates = "', '".join([date for date in trade_dates])
        query = f"""
        SELECT ts_code, trade_date, open, close
        FROM idx_factor_pro
        WHERE ts_code = '000001.SH'
        AND trade_date IN ('{dates}')
        """

        try:
            df = pd.read_sql(query, self.engine, parse_dates=['trade_date'])
        except Exception as e:
            logging.error(f"无法获取基准指数数据 - {e}")
            return pd.DataFrame()

        if df.empty:
            logging.warning("未找到基准指数数据。")
            return df

        df['trade_date'] = df['trade_date'].dt.strftime('%Y-%m-%d')
        logging.info("获取上证指数数据:")
        logging.debug(df.head())
        df = df.sort_values('trade_date')
        df['daily_return'] = df['close'].pct_change().fillna(0)
        df['cumulative_return'] = (1 + df['daily_return']).cumprod() - 1
        df['cumulative_return'] = df['cumulative_return'] * 100
        return df


class SellTax(bt.CommInfoBase):
    """
    自定义佣金和卖出税费类。
    """
    params = (
        ('commission', 0.001),  # 佣金比例
        ('tax', 0.001),          # 卖出税费比例
        ('stocklike', True),
        ('commtype', bt.CommInfoBase.COMM_PERC),
        ('min_comm', 0.0),
    )

    def _getcommission(self, size, price, pseudoexec):
        """
        根据交易类型计算佣金和税费。
        """
        if size > 0:  # 买入
            return abs(size) * price * self.p.commission
        elif size < 0:  # 卖出
            return abs(size) * price * (self.p.commission + self.p.tax)
        return 0.0


class StrategyHandler(bt.Strategy):
    """
    轮动策略，依据技术因子进行股票选择和调仓。
    """
    params = (
        ('rotation_days', 30),      # 每次轮动的天数
        ('num_stocks', 5),          # 每次轮动时选择的股票数量
        ('df_factors', None),       # 股票因子数据
        ('rotation_dates', None),   # 预先计算的轮动交易日期
        ('last_trade_date', None),  # 最后一个交易日
        ('stop_profit', 0.1),       # 止盈比例
        ('stop_loss', 0.05),        # 止损比例
        ('fee',0.00025),
        ('tax',0.0005)
    )

    def __init__(self):
        self.current_stocks = []  # 当前投资组合中的股票（存储股票代码）
        self.portfolio_values = []  # 用于存储每次交易的投资组合价值
        self.df_factors = self.params.df_factors.copy() if self.params.df_factors is not None else pd.DataFrame()
        self.rotation_dates = set(self.params.rotation_dates) if self.params.rotation_dates else set()
        self.last_trade_date = self.params.last_trade_date

        self.stop_profit = self.params.stop_profit
        self.stop_loss = self.params.stop_loss

        if not self.df_factors.empty:
            # 将因子数据中的 trade_date 转换为字符串格式以便匹配
            self.df_factors['trade_date'] = self.df_factors['trade_date'].dt.strftime('%Y-%m-%d')

        # 初始化用于策略评估的变量
        self.trade_results = []  # 存储每笔交易的盈亏
        self.consecutive_wins = 0
        self.consecutive_losses = 0
        self.max_consecutive_wins = 0
        self.max_consecutive_losses = 0
        self.daily_returns = []  # 存储每日收益率
        self.dates = []  # 记录交易日期
        self.sell_mount = 0
        self.tax = self.params.tax
        self.fee =self.params.fee

        # 引入一个标志变量，用于跟踪是否在提交买单
        self.buying = False

        # 初始化交易日志列表
        self.trade_log = []

        # 自定义现金余额变量
        self.available_cash = self.broker.getcash()

    def next(self):
        """
        每次新的一天调用该方法，判断是否需要进行轮动调整。
        """
        current_date = self.datas[0].datetime.date(0).strftime('%Y-%m-%d')  # 获取当前的交易日期
        logging.debug(f"当前日期: {current_date}")  # 添加调试信息以检查日期
        # logging.info(f"当前日期: {current_date}")  # 添加调试信息以检查日期

        self.dates.append(current_date)  # 记录交易日期
        current_value = self.broker.getvalue()
        self.portfolio_values.append(current_value)  # 记录当前投资组合的总价值

        # 计算每日收益率
        if len(self.portfolio_values) > 1:
            previous_value = self.portfolio_values[-2]
            daily_return = (current_value - previous_value) / previous_value
            self.daily_returns.append(daily_return)
        else:
            self.daily_returns.append(0)

        # 记录当前现金和投资组合总价值
        logging.debug(f"当前现金: {self.available_cash:.2f}, 投资组合总价值: {current_value:.2f}")

        # 检查是否需要进行轮动调整
        if current_date in self.rotation_dates and not self.buying:
            self.rebalance_portfolio(current_date)

        # 在最后一个交易日清仓所有股票
        if current_date == self.last_trade_date:
            self.close_all_positions(current_date)

        # 检查止盈止损
        self.check_stop_profit_loss(current_date)

    def check_stop_profit_loss(self, current_date):
        """
        检查持仓股票是否触发止盈或止损条件。
        """
        for stock in self.current_stocks.copy():
            data = self.getdatabyname(stock)
            if data is None:
                logging.error(f"未找到股票数据 {stock}")
                continue

            # 检查是否停牌
            if data.lines.is_suspended[0]:
                logging.info(f"{current_date} 股票 {stock} 停牌，跳过止盈止损检查。")
                continue

            open_price = data.open[0]
            # 获取买入价格
            position = self.getposition(data)
            if position.size <= 0:
                continue
            buy_price = position.price

            # 计算涨跌幅
            price_change = (open_price - buy_price) / buy_price
         
            # 检查止盈
            if price_change >= self.stop_profit:
                logging.info(f"{current_date} 股票 {stock} 达到止盈点: 涨幅 {price_change*100:.2f}%")
                self.close_position(stock, current_date)

            # 检查止损
            elif price_change <= -self.stop_loss:
                logging.info(f"{current_date} 股票 {stock} 达到止损点: 跌幅 {price_change*100:.2f}%")
                self.close_position(stock, current_date)

    def rebalance_portfolio(self, current_date):
        """
        进行投资组合的调仓操作。
        """
        logging.info(f"\n{current_date} - 开始轮动调整投资组合...")
        
        # 清仓之前的股票
        for stock_ts_code in self.current_stocks.copy():
            self.close_position(stock_ts_code, current_date)

        # 获取评分最高的前n个股票
        selected_stocks = self.calculate_and_select_scores(current_date)

        if selected_stocks.empty:
            logging.warning("没有可选的股票评分，跳过本次轮动。")
            return

        # 输出选中股票的评分
        self.print_selected_stocks(selected_stocks)

        # 设置标志，表示正在买入
        self.buying = True

        # 提交买入订单
        self.buy_new_stocks(current_date, selected_stocks)

    def close_position(self, stock_ts_code, current_date):
        """
        卖出持有的股票，并从持仓列表中移除，同时记录交易日志。
        """
        data = self.getdatabyname(stock_ts_code)
        if data is None:
            logging.error(f"未找到股票数据 {stock_ts_code}")
            return

        position = self.getposition(data)
        if position.size > 0:
            # 检查当天是否停牌
            if data.lines.is_suspended[0]:
                logging.info(f"{current_date} 股票 {stock_ts_code} 停牌，无法卖出。")
                return

            # 记录卖出前的持仓数量
            logging.debug(f"{current_date} 卖出前持仓数量: {position.size} 股，股票代码: {stock_ts_code}")

            # 获取开盘价
            open_price = data.open[0]

            # 使用 self.sell 提交卖出订单，确保数量为正数，并以开盘价卖出
            self.sell(data=data, size=abs(position.size), price=open_price)
            logging.info(f"{current_date} 提交卖出订单: {stock_ts_code}, 数量: {abs(position.size)}, 开盘价: {open_price:.2f}")
            self.available_cash += abs(position.size)*open_price*(1-self.tax-self.fee)
        else:
            logging.info(f"{current_date} 没有持有股票: {stock_ts_code}，无需卖出。")

    def buy_new_stocks(self, current_date, selected_stocks):
        """
        买入选中的股票。
        """

        allocation_per_stock = (self.available_cash * 0.8) / self.params.num_stocks  # 每只股票分配的资金
        logging.debug(f"总可用资金: {self.available_cash:.2f}, 每只股票分配资金: {allocation_per_stock:.2f}")

        remaining_stocks = self.params.num_stocks

        for _, row in selected_stocks.iterrows():
            ts_code = row['ts_code']
            data = self.getdatabyname(ts_code)
            if data is None:
                logging.error(f"未找到股票数据 {ts_code}")
                continue

            # 检查当天是否停牌
            if data.lines.is_suspended[0]:
                logging.info(f"{current_date} 股票 {ts_code} 停牌，无法买入。")
                continue

            # 检查当天是否涨停
            if data.lines.is_limit_up[0]:
                logging.info(f"{current_date} 股票 {ts_code} 涨停，无法买入。")
                continue

            price = data.open[0]  # 使用开盘价

            allocation = min(allocation_per_stock, self.available_cash / remaining_stocks)
            max_size = int(allocation // (price * 100)) * 100  # 计算最多可买入的数量，单位是100

            logging.debug(f"准备买入股票: {ts_code}, 价格: {price:.2f}, 分配资金: {allocation:.2f}, 买入数量: {max_size}")

            if max_size > 0:
                self.buy(data=data, size=max_size, price=price)
                total_amount = price * max_size
                remaining_stocks -= 1
                

                logging.info(f"{current_date} 提交买入订单: {ts_code}, 成交价格: {price:.2f}, 买入数量: {max_size}, "
                             f"总金额: {total_amount:.2f}, 预计剩余现金: {self.available_cash:.2f}")
                self.available_cash -= total_amount*(1+self.fee)

                # 记录买入交易日志（现金将在订单完成时更新）
                trade_record = {
                    "code": ts_code,
                    "date": current_date,
                    "type": "buy",
                    "price": round(price, 2),
                    "amount": max_size,
                    "total": round(total_amount, 2),
                    "current": round(self.available_cash, 2)  # 使用自定义的现金余额
                }
                self.trade_log.append(trade_record)

                # 确保股票已添加到当前持仓
                if ts_code not in self.current_stocks:
                    self.current_stocks.append(ts_code)

            else:
                logging.info(f"{current_date} 资金不足，无法买入股票: {ts_code}")

    def close_all_positions(self, current_date):
        """
        清仓所有持有的股票。
        """
        logging.info(f"{current_date} - 清仓所有股票")
        for stock_ts_code in self.current_stocks.copy():
            self.close_position(stock_ts_code, current_date)

    @staticmethod
    def get_factors():
        # 返回需要查询的因子名称列表
        factor_list = get_factor_list()    
        return factor_list

    def calculate_and_select_scores(self, current_date):
        """
        计算所有股票的评分，并选择评分最高的前n个股票。
        """
        # 过滤出当前日期的因子数据
        current_factors = self.df_factors[self.df_factors['trade_date'] == current_date].copy()

        if current_factors.empty:
            logging.warning(f"没有找到日期 {current_date} 的因子数据。")
            return pd.DataFrame()
        

        current_factors = get_factor_score(current_factors)
        logging.debug(f"当前日期 {current_date} 的评分数据:\n{current_factors[['ts_code', 'score']].head()}")

        # 合并开盘价数据
        open_prices = pd.DataFrame({
            'ts_code': [data._name for data in self.datas],
            'open': [data.open[0] for data in self.datas]
        })
        current_factors = current_factors.merge(open_prices, on='ts_code', how='left')

        # 排序并选择前n个股票
        selected = current_factors.sort_values(by='score', ascending=False).head(self.params.num_stocks)

        logging.debug(f"选中股票:\n{selected[['ts_code', 'score']].to_string(index=False)}")

        return selected

    def print_selected_stocks(self, selected_stocks):
        """
        输出选中的股票。
        """
        logging.info(f"选股评分最高的前{self.params.num_stocks}支股票:")
        for _, row in selected_stocks.iterrows():
            logging.info(f"股票: {row['ts_code']}, 评分: {row['score']:.2f}")

    def notify_order(self, order):
        """
        订单状态通知。
        """
        if order.status in [order.Submitted, order.Accepted]:
            # 订单已提交/接受，尚未完成
            return

        current_date = self.datas[0].datetime.date(0).strftime('%Y-%m-%d')

        if order.status == order.Completed:
            if order.isbuy():
                logging.info(f"{current_date} 买单完成: {order.data._name}, 成交价格: {order.executed.price:.2f}, 数量: {order.executed.size}")
                # 更新自定义现金余额
                commission_info = self.broker.getcommissioninfo(order.data)
                commission = commission_info.p.commission
                total_cost = order.executed.price * order.executed.size * (1 + commission)
              

                # 记录交易日志
                trade_record = {
                    "code": order.data._name,
                    "date": current_date,
                    "type": "buy",
                    "price": round(order.executed.price, 2),
                    "amount": order.executed.size,
                    "total": round(total_cost, 2),
                    "current": round(self.available_cash, 2)  # 使用自定义的现金余额
                }
                self.trade_log.append(trade_record)
            elif order.issell():
                logging.info(f"{current_date} 卖单完成: {order.data._name}, 成交价格: {order.executed.price:.2f}, 数量: {order.executed.size}")
                # 更新自定义现金余额
                commission_info = self.broker.getcommissioninfo(order.data)
                commission = commission_info.p.commission
                tax = commission_info.p.tax
                total_gain = order.executed.price * order.executed.size * (1 - commission - tax)
            

                # 记录交易日志
                trade_record = {
                    "code": order.data._name,
                    "date": current_date,
                    "type": "sell",
                    "price": round(order.executed.price, 2),
                    "amount": order.executed.size,
                    "total": round(total_gain, 2),
                    "current": round(self.available_cash, 2)  # 使用自定义的现金余额
                }
                self.trade_log.append(trade_record)
                # 从当前持仓中移除
                if order.data._name in self.current_stocks:
                    self.current_stocks.remove(order.data._name)
        elif order.status in [order.Canceled, order.Margin, order.Rejected]:
            logging.warning(f"{current_date} 订单被取消/保证金不足/拒绝: {order.getstatusname()}")

        # 当所有买单和卖单完成后，重置买入标志
        if self.buying and order.status in [order.Completed, order.Canceled, order.Margin, order.Rejected]:
            # 检查是否还有未完成的订单
            if not any([o.status in [o.Submitted, o.Accepted] for o in self.broker.orders]):
                self.buying = False

    def notify_trade(self, trade):
        """
        交易完成通知。
        """
        if not trade.isclosed:
            return

        current_date = self.datas[0].datetime.date(0).strftime('%Y-%m-%d')
        profit = trade.pnl  # 单笔交易的盈亏

        logging.info(f"{current_date} 交易完成: {trade.data._name}, 盈亏: {profit:.2f}")

        # 更新连续胜利和连续亏损天数
        if profit > 0:
            self.consecutive_wins += 1
            self.consecutive_losses = 0
            if self.consecutive_wins > self.max_consecutive_wins:
                self.max_consecutive_wins = self.consecutive_wins
        elif profit < 0:
            self.consecutive_losses += 1
            self.consecutive_wins = 0
            if self.consecutive_losses > self.max_consecutive_losses:
                self.max_consecutive_losses = self.consecutive_losses

        # 计算本次交易的收益率
        cost_basis = trade.size * trade.price  # 交易成本
        trade_return = (profit / cost_basis) * 100 if cost_basis != 0 else 0
        self.trade_results.append(trade_return)

    def stop(self):
        """
        策略结束时同步自定义现金余额与broker现金余额。
        """
        logging.info("策略结束，同步现金余额。")
        self.available_cash = self.broker.getcash()


class BacktestManager:
    """
    主进程模块
    定义回测框架的整体运行状态，聚合和统一修改参数。
    """
    def __init__(self, db_url, stock_pool, start_date, end_date, rotation_days=30, num_stocks=5,
                 init_money=1000000, commission=0.001, tax=0.001, slippage=0.001,
                 stop_profit=0.1, stop_loss=0.05):
        self.db_url = db_url
        self.stock_pool = stock_pool
        self.start_date = start_date
        self.end_date = end_date
        self.rotation_days = rotation_days
        self.num_stocks = num_stocks
        self.init_money = init_money
        self.commission = commission  # 交易佣金比例
        self.tax = tax                # 交易税比例
        self.slippage = slippage      # 滑点比例
        self.stop_profit = stop_profit  # 止盈百分比
        self.stop_loss = stop_loss      # 止损百分比
        # 初始化数据处理器
        self.data_handler = DataHandler(db_url)

    def calculate_rotation_dates(self, trade_dates, rotation_days):
        """
        计算所有轮动的交易日期。
        从第一个交易日开始，每隔 rotation_days 个交易日进行一次轮动。
        """
        rotation_dates = []
        total_days = len(trade_dates)
        index = 0
        while index < total_days:
            rotation_dates.append(trade_dates[index])
            index += rotation_days
        logging.debug(f"Calculated rotation_dates: {rotation_dates}")
        return rotation_dates

    def run_backtest(self):
        start_time = time.time()  # 记录开始时间

        # 创建Cerebro引擎实例
        cerebro = bt.Cerebro()

        # 添加观察者以监控现金余额和投资组合总价值
        cerebro.addobserver(bt.observers.Broker)  # 观察现金余额
        cerebro.addobserver(bt.observers.Value)    # 观察投资组合总价值
        cerebro.addobserver(bt.observers.Trades)   # 观察交易执行情况

        # 添加滑点
        cerebro.broker.set_slippage_perc(self.slippage, slip_open=True, slip_limit=True, slip_match=True, slip_out=True)

        # 设置佣金和税费
        cerebro.broker.setcommission(commission=self.commission, mult=1.0, name=None, percabs=True)

        # 添加自定义佣金信息
        cerebro.broker.addcommissioninfo(SellTax())

        # 计算需要查询的交易日期
        all_trade_dates = self.data_handler.get_trade_dates(self.start_date, self.end_date)

        if not all_trade_dates:
            logging.warning("无法进行回测，因为没有有效的交易日期。")
            return

        # 打印交易日期的前10个和最后10个，以确认数据完整性
        logging.info(f"所有交易日期的前10个: {all_trade_dates[:10]}")
        logging.info(f"所有交易日期的最后10个: {all_trade_dates[-10:]}")

        # 计算轮动交易日期
        rotation_dates = self.calculate_rotation_dates(all_trade_dates, self.rotation_days)
        logging.info(f"轮动交易日期的前10个: {rotation_dates[:10]}...（共 {len(rotation_dates)} 个轮动日期）")

        # 从MySQL数据库加载因子数据
        factors = StrategyHandler.get_factors()
        factor_df = self.data_handler.get_technical_factors(self.stock_pool, all_trade_dates, factors)

        if factor_df.empty:
            logging.warning("无法进行回测，因为没有有效的因子数据。")
            return

        # 确认因子数据包含所有轮动日期
        missing_dates = set(rotation_dates) - set(factor_df['trade_date'].dt.strftime('%Y-%m-%d'))
        if missing_dates:
            logging.warning(f"因子数据缺失以下轮动日期: {missing_dates}")

        # 添加策略并传递因子数据和轮动交易日期
        cerebro.addstrategy(
            StrategyHandler,
            df_factors=factor_df,          # 传递因子数据
            rotation_days=self.rotation_days,
            num_stocks=self.num_stocks,
            rotation_dates=rotation_dates, # 传递轮动交易日期
            last_trade_date=all_trade_dates[-2],  # 传递最后一个交易日
            stop_profit=self.stop_profit,
            stop_loss=self.stop_loss,
            tax = self.tax,
            fee = self.commission
        )

        # 从MySQL数据库加载日线数据
        data_dict = self.data_handler.get_data_from_mysql(self.stock_pool, all_trade_dates)
        if not data_dict:
            logging.warning("无法进行回测，因为没有有效的日线数据。")
            return

        for ts_code, data in data_dict.items():
            cerebro.adddata(data)

        # 获取基准指数数据
        benchmark_df = self.data_handler.get_benchmark_data(all_trade_dates)
        if benchmark_df.empty:
            logging.warning("无法获取基准指数数据。")
            return

        # 设置初始现金
        cerebro.broker.setcash(self.init_money)

        # 打印初始投资组合价值
        initial_value = cerebro.broker.getvalue()

        logging.info(f'初始投资组合价值: {initial_value:.2f}')

        # 添加分析器
        cerebro.addanalyzer(bt.analyzers.DrawDown, _name='drawdown')
        cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name='trade_analyzer')
        cerebro.addanalyzer(bt.analyzers.Returns, _name='returns')
        cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name='sharpe_ratio', riskfreerate=0.01)  # 设置年化无风险利率为1%

        # 运行回测
        strategies = cerebro.run()
        strategy = strategies[0]

        # 记录结束时间
        end_time = time.time()
        total_time = end_time - start_time
        logging.info(f"\n回测运行时间: {total_time:.2f} 秒")

        # 打印最终投资组合价值
        final_value = cerebro.broker.getvalue()
        final_cash = cerebro.broker.getcash()
        logging.info(f'最终投资组合价值: {final_value:.2f}')
        logging.info(f'最终现金: {final_cash:.2f}')
        # 计算并打印回测结果指标
        strategy_return = final_value - initial_value
        strategy_return_rate = ((strategy_return) / initial_value) * 100
        logging.info(f'策略收益: {strategy_return:.2f}')
        logging.info(f'收益率: {strategy_return_rate:.2f}%')

        drawdown = strategy.analyzers.drawdown.get_analysis()
        trade_analyzer = strategy.analyzers.trade_analyzer.get_analysis()
        returns = strategy.analyzers.returns.get_analysis()
        sharpe_ratio_analyzer = strategy.analyzers.sharpe_ratio.get_analysis()
        # print("returns:", returns)
        # print("sharpe:", sharpe_ratio_analyzer)

        # 计算胜率
        winning_trades = 0
        total_trades = 0

        # 检查 'won' 键是否存在
        if 'won' in trade_analyzer and 'total' in trade_analyzer['won']:
            winning_trades = trade_analyzer['won']['total']
        else:
            winning_trades = 0

        # 检查 'total' 键是否存在，并且 'open' 和 'closed' 键是否存在
        if 'total' in trade_analyzer:
            total_open = trade_analyzer['total'].get('open', 0)
            total_closed = trade_analyzer['total'].get('closed', 0)
            total_trades = total_open + total_closed
        else:
            total_trades = 0

        if total_trades > 0:
            win_rate = (winning_trades / total_trades) * 100
        else:
            win_rate = 0

        logging.info(f"胜率: {win_rate:.2f}%")

        # 处理 Sharpe 比率
        if sharpe_ratio_analyzer and 'sharperatio' in sharpe_ratio_analyzer and sharpe_ratio_analyzer['sharperatio'] is not None:
            sharpe = sharpe_ratio_analyzer['sharperatio']
            logging.info(f"夏普比率: {sharpe:.4f}")
        else:
            sharpe = 0
            logging.info("夏普比率: 无数据")

        # 其他指标的处理（确保所有分析器结果都进行相应的检查）
        # 例如，计算最大回撤
        if 'max' in drawdown and 'drawdown' in drawdown['max']:
            max_drawdown = drawdown['max']['drawdown']
        else:
            max_drawdown = 0
        logging.info(f"最大回撤: {max_drawdown:.2f}%")

        # 计算基准收益
        # 使用基准的日收益率累积计算
        benchmark_df_sorted = benchmark_df.sort_values('trade_date')
        benchmark_df_sorted['daily_return'] = benchmark_df_sorted['close'].pct_change().fillna(0)
        benchmark_df_sorted['cumulative_return'] = (1 + benchmark_df_sorted['daily_return']).cumprod() - 1
        benchmark_df_sorted['cumulative_return'] = benchmark_df_sorted['cumulative_return'] * 100
        benchmark_return = benchmark_df_sorted['cumulative_return'].iloc[-1]
        logging.info(f"基准收益: {benchmark_return:.2f}%")

        # 计算Alpha和Beta
        # 首先计算策略的日收益率
        strategy_daily_returns = pd.Series(strategy.daily_returns, index=pd.to_datetime(strategy.dates))
        if strategy_daily_returns.iloc[0] == 0:
            strategy_daily_returns = strategy_daily_returns.iloc[1:]
        strategy_daily_returns = strategy_daily_returns[~strategy_daily_returns.index.duplicated(keep='first')]

        # 计算基准的日收益率
        benchmark_returns = benchmark_df_sorted['daily_return']
        benchmark_returns.index = pd.to_datetime(benchmark_df_sorted['trade_date'])

        # 对齐日期
        combined_returns = pd.concat([strategy_daily_returns, benchmark_returns], axis=1, join='inner')
        combined_returns.columns = ['strategy', 'benchmark']

        # 计算Beta和Alpha
        covariance = combined_returns['strategy'].cov(combined_returns['benchmark'])
        benchmark_variance = combined_returns['benchmark'].var()
        beta = covariance / benchmark_variance if benchmark_variance != 0 else 0
        alpha = combined_returns['strategy'].mean() - beta * combined_returns['benchmark'].mean()

        logging.info(f"Beta: {beta:.4f}")
        logging.info(f"Alpha: {alpha:.4f}")

        # 计算夏普比率
        # 使用backtrader的分析器结果
        if 'sharpe_ratio' in sharpe_ratio_analyzer and 'sharperatio' in sharpe_ratio_analyzer and sharpe_ratio_analyzer['sharperatio'] is not None:
            sharpe = sharpe_ratio_analyzer['sharperatio']
            logging.info(f"夏普比率 (Backtrader 分析器): {sharpe:.4f}")
        else:
            # 如果backtrader的分析器没有结果，使用手动计算
            risk_free_rate = 0.01  # 年化无风险利率为1%
            excess_returns = strategy_daily_returns - risk_free_rate / 252
            sharpe = excess_returns.mean() / excess_returns.std() * np.sqrt(252) if excess_returns.std() != 0 else 0
            logging.info(f"夏普比率 (手动计算): {sharpe:.4f}")

        # 计算最大连涨天数和最大连跌天数
        consecutive_gains = (strategy_daily_returns > 0).astype(int).groupby(
            (strategy_daily_returns <= 0).astype(int).cumsum()).cumsum()
        max_consecutive_gains = consecutive_gains.max() if not consecutive_gains.empty else 0

        consecutive_losses = (strategy_daily_returns < 0).astype(int).groupby(
            (strategy_daily_returns >= 0).astype(int).cumsum()).cumsum()
        max_consecutive_losses = consecutive_losses.max() if not consecutive_losses.empty else 0

        logging.info(f"最大连涨天数: {int(max_consecutive_gains)} 天")
        logging.info(f"最大连跌天数: {int(max_consecutive_losses)} 天")

        # 准备绘图数据
        # 生成策略的累计净收益
        strategy_cumulative_returns = (1 + pd.Series(strategy.daily_returns).fillna(0)).cumprod() - 1
        strategy_cumulative_returns = strategy_cumulative_returns * 100  # 转换为百分比

        # 确保benchmark_df_sorted的日期与strategy_cumulative_returns的日期一致
        strategy_dates = pd.to_datetime(strategy.dates)
        benchmark_df_sorted['trade_date'] = pd.to_datetime(benchmark_df_sorted['trade_date'])
        benchmark_df_sorted = benchmark_df_sorted.set_index('trade_date').reindex(strategy_dates).fillna(method='ffill').reset_index()
        benchmark_df_sorted.rename(columns={'index': 'trade_date'}, inplace=True)

        # 生成基准的累计净收益
        benchmark_cumulative_returns = benchmark_df_sorted['cumulative_return']

        # 转换为列表
        dates_list = strategy_dates.strftime('%Y-%m-%d').tolist()
        strategy_returns_list = strategy_cumulative_returns.tolist()
        benchmark_returns_list = benchmark_cumulative_returns.tolist()

        # 使用 pyecharts 绘制可交互的累计收益曲线

        # 保留两位小数的策略和基准收益列表
        strategy_returns_list_rounded = [round(x, 2) for x in strategy_returns_list]
        benchmark_returns_list_rounded = [round(x, 2) for x in benchmark_returns_list]

        line = (
            Line()
            .add_xaxis(dates_list)
            .add_yaxis(
                "策略收益",
                strategy_returns_list_rounded,
                is_smooth=True,
                label_opts=opts.LabelOpts(is_show=False),
                linestyle_opts=opts.LineStyleOpts(color="#c23531")
            )
            .add_yaxis(
                "基准收益",
                benchmark_returns_list_rounded,
                is_smooth=True,
                label_opts=opts.LabelOpts(is_show=False),
                linestyle_opts=opts.LineStyleOpts(color="#2f4554")
            )
            .set_global_opts(
                title_opts=opts.TitleOpts(title="策略与基准的累计收益对比"),
                tooltip_opts=opts.TooltipOpts(
                    trigger="axis",
                    axis_pointer_type="cross",
                    formatter="{b}<br/>策略收益: {c0}%<br/>基准收益: {c1}%",
                    extra_css_text="background-color: rgba(255,255,255,0.8);"
                ),
                legend_opts=opts.LegendOpts(pos_top="10%"),
                xaxis_opts=opts.AxisOpts(
                    type_="category",
                    boundary_gap=False,
                    axislabel_opts=opts.LabelOpts(rotate=45),
                ),
                yaxis_opts=opts.AxisOpts(
                    type_="value",
                    name="累计净收益率 (%)",
                    axislabel_opts=opts.LabelOpts(formatter="{value}%")
                ),
                datazoom_opts=[
                    opts.DataZoomOpts(),
                    opts.DataZoomOpts(type_="inside")
                ],
                toolbox_opts=opts.ToolboxOpts(),
            )
        )

        # 创建页面并添加图表
        page = Page(layout=Page.SimplePageLayout)
        page.add(line)
        page.render("strategy_vs_benchmark.html")
        logging.info("\n策略与基准的累计收益对比图已保存为 'strategy_vs_benchmark.html'。")

        # 将交易日志写入JSON文件
        trade_log_filename = "trade_log.json"
        try:
            with open(trade_log_filename, 'w', encoding='utf-8') as f:
                json.dump(strategy.trade_log, f, ensure_ascii=False, indent=4)
            logging.info(f"交易日志已保存为 '{trade_log_filename}'。")
        except Exception as e:
            logging.error(f"无法写入交易日志文件 - {e}")

        # 额外输出评估指标
        logging.info("\n=== 策略评估指标 ===")
        logging.info(f"策略收益: {strategy_return:.2f}")
        logging.info(f"收益率: {strategy_return_rate:.2f}%")
        logging.info(f"基准收益: {benchmark_return:.2f}%")
        logging.info(f"Alpha: {alpha:.4f}")
        logging.info(f"Beta: {beta:.4f}")
        logging.info(f"夏普比率: {sharpe:.4f}" if sharpe else "夏普比率: 无数据")
        logging.info(f"最大回撤: {max_drawdown:.2f}%")
        logging.info(f"胜率: {win_rate:.2f}%")
        logging.info(f"最大连涨天数: {int(max_consecutive_gains)} 天")
        logging.info(f"最大连跌天数: {int(max_consecutive_losses)} 天")


if __name__ == '__main__':
    # 数据库连接URL
    DB_URL = 'mysql+pymysql://quantuser:Quantuser233.@bj-cynosdbmysql-grp-3upmvv08.sql.tencentcdb.com:27017/stock'
    # data = DataHandler(db_url=DB_URL)
    # stock_pool = data.get_stock_code_lsit()
    # 示例用法:
    # 使用上证五十的成分股进行测试
    # stock_pool = [
    #     '600000.SH', '600016.SH', '600019.SH', '600028.SH', '600029.SH',
    #     '600030.SH', '600036.SH', '600048.SH', '600050.SH', '600104.SH',
    #     '600111.SH', '600196.SH', '600276.SH', '600309.SH', '600340.SH',
    #     '600519.SH', '600547.SH', '600570.SH', '600585.SH', '600588.SH',
    #     '600690.SH', '600703.SH', '600745.SH', '600837.SH', '600887.SH',
    #     '600893.SH', '600900.SH', '600919.SH', '600958.SH', '600999.SH',
    #     '601012.SH', '601066.SH', '601088.SH', '601138.SH', '601166.SH',
    #     '601169.SH', '601186.SH', '601211.SH', '601229.SH', '601288.SH',
    #     '601318.SH', '601319.SH', '601336.SH', '601390.SH', '601398.SH',
    #     '601601.SH', '601628.SH', '601668.SH', '601688.SH', '601818.SH'
    # ]
    stock_pool_map = {
        "上证指数": '000001.SH',
        "深证成指": '399001.SZ',
        "上证50": '000016.SH',
        "中证500": '000905.SH',
        "中小100": '399005.SZ',
    }
    # 定义回测参数
    START_DATE = '2023-01-10'
    END_DATE = '2023-02-01'
    ROTATION_DAYS = 20
    NUM_STOCKS = 5
    INITIAL_MONEY = 1000000
    COMMISSION = 0.00025  # 交易佣金比例
    TAX = 0.0005         # 交易税比例
    SLIPPAGE = 0.001    # 滑点比例
    STOP_PROFIT = 0.2    # 止盈百分比
    STOP_LOSS = 0.1    # 止损百分比
    STOCK_POOL_TYPE = "中小100"


    data = DataHandler(db_url=DB_URL)
    stock_pool = data.get_index_code_list(stock_pool_map[STOCK_POOL_TYPE])
    print("定义的股票池有以下代码：",stock_pool)
    # 均以开盘价买入、在结束日期的前一个交易日卖出
    # 创建并运行回测管理器
    manager = BacktestManager(
        db_url=DB_URL,
        stock_pool=stock_pool,
        start_date=START_DATE,
        end_date=END_DATE,
        rotation_days=ROTATION_DAYS,
        num_stocks=NUM_STOCKS,
        init_money=INITIAL_MONEY,
        commission=COMMISSION,
        tax=TAX,
        slippage=SLIPPAGE,
        stop_profit=STOP_PROFIT,
        stop_loss=STOP_LOSS
    )
    manager.run_backtest()

# 所需依赖:
# - backtrader: 用于回测交易策略的Python库
# - sqlalchemy: 用于连接MySQL数据库的库
# - pandas: 用于数据处理的库
# - numpy: 用于数值计算的库
# - pyecharts: 用于绘图的库
# - pymysql: MySQL连接驱动
# 安装命令:
# pip install backtrader sqlalchemy pandas numpy pyecharts pymysql
