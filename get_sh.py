import tushare as ts
import pandas as pd
from sqlalchemy import create_engine

def get_index_daily_basic(ts_code, start_date, end_date):
    """
    获取指定指数在指定日期范围内的每日指标数据
    :param ts_code: str, TS代码
    :param start_date: str, 开始日期
    :param end_date: str, 结束日期
    :return: DataFrame, 包含大盘指数每日指标数据
    """
    pro = ts.pro_api('api-key')
    df = pro.index_dailybasic(ts_code=ts_code, start_date=start_date, end_date=end_date
                        )
    return df

def save_to_mysql(df, table_name='index_daily_basic'):
    """
    将DataFrame保存到MySQL数据库
    :param df: DataFrame, 要保存的数据
    :param table_name: str, 数据库表名
    """
    engine = create_engine('mysql+pymysql://quant:Quant233.@bj-cynosdbmysql-grp-3upmvv08.sql.tencentcdb.com:27017/stock')
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    df.to_sql(table_name, engine, if_exists='append', index=False)

# 指数代码列表
index_codes = ['000001.SH', '399001.SZ', '000016.SH', '000905.SH', '399005.SZ', '399006.SZ']

# 遍历每个指数代码并获取数据
for code in index_codes:
    df = get_index_daily_basic(ts_code=code, start_date='20040101', end_date='20091231')
    save_to_mysql(df)
