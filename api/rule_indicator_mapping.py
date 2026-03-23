SCORE_BUCKET_MAPPING = {
    "大盘": "trend",
    "个股基础": "structure",
    "趋势动量": "trend",
    "量能": "volume",
    "板块相对": "trend",
    "左侧潜伏": "structure",
    "右侧突破": "structure",
    "风险观察": "structure",
    "止盈止损": "rr",
    "时间周期": "structure",
}


def get_default_mapping() -> dict:
    return dict(SCORE_BUCKET_MAPPING)
