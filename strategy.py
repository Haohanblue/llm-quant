def get_factor_score(current_factors):
     # 计算 RSI 平均值
    # current_factors['rsi_avg'] = current_factors[['rsi_bfq_6', 'rsi_bfq_12', 'rsi_bfq_24']].mean(axis=1)

    # 计算评分，假设评分公式如下：
    # score = -pe - pb - ps + rsi_avg
    # (mtm_hfq * 0.1) + (macd_hfq * 0.2) + (rsi_hfq_12 * 0.0667) + (brar_ar_hfq * 0.0833) + (psy_hfq * 0.0333) + (trix_hfq * 0.0833) + (rsi_hfq_24 * 0.0833) + (brar_br_hfq * 0.0667) + (psyma_hfq * 0.05) + (obv_hfq * 0.05) + (rsi_hfq_6 * 0.0833) + (vr_hfq * 0.0667) + (dmi_adx_hfq * 0.0333)
    # 您可以根据实际的评分公式进行调整
    # current_factors['score'] = (
    #     -0.1*current_factors['pe'].apply(lambda x: x if x > 0 else 0) -
    #     0.2*current_factors['pb'].apply(lambda x: x if x > 0 else 0) -
    #     0.3*current_factors['ps'].apply(lambda x: x if x > 0 else 0) 
    # )
    df = current_factors
    current_factors['score'] = ((df['macd_dif_hfq'] * df['psy_hfq']) + ((df['rsi_hfq_12'] + df['brar_ar_hfq'] - df['brar_br_hfq']) * df['mtm_hfq'] / (1 + df['atr_hfq']))) / 2
    return current_factors
def get_factor_list():
    factor_list = [
    "macd_dif_hfq",
    "psy_hfq",
    "rsi_hfq_12",
    "brar_ar_hfq",
    "brar_br_hfq",
    "mtm_hfq",
    "atr_hfq"
  ]
    return factor_list