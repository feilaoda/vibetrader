# Common ETFs and Indices for Fallback
FALLBACK_ETFS = [
    {"code": "510050", "name": "50ETF"},
    {"code": "510300", "name": "300ETF"},
    {"code": "510500", "name": "500ETF"},
    {"code": "159915", "name": "创业板"},
    {"code": "588000", "name": "科创50"},
    {"code": "512880", "name": "证券ETF"},
    {"code": "512000", "name": "券商ETF"},
    {"code": "512660", "name": "军工ETF"},
    {"code": "512690", "name": "酒ETF"},
    {"code": "512480", "name": "半导体"},
    {"code": "515030", "name": "新能源车"},
    {"code": "513050", "name": "中概互联"},
    {"code": "513180", "name": "恒生科技"},
    {"code": "159949", "name": "创业板50"},
    {"code": "510180", "name": "180ETF"},
    {"code": "512010", "name": "医药ETF"},
    {"code": "512170", "name": "医疗ETF"},
    {"code": "515790", "name": "光伏ETF"},
    {"code": "512760", "name": "芯片ETF"},
    {"code": "515050", "name": "5G ETF"},
    {"code": "159995", "name": "芯片ETF"},
    {"code": "159605", "name": "中概互联"},
    {"code": "510900", "name": "H股ETF"},
    {"code": "159920", "name": "恒生ETF"},
    {"code": "513500", "name": "标普500"},
    {"code": "513100", "name": "纳指ETF"},
]

def get_fallback_etfs():
    import pandas as pd
    return pd.DataFrame(FALLBACK_ETFS)
