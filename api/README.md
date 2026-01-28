# VibeTrader AKShare API

A股数据代理服务，基于 FastAPI + AKShare。

## 安装

```bash
cd api
pip install -r requirements.txt
```

## 启动服务

```bash
# 开发模式
uvicorn main:app --reload --host 0.0.0.0 --port 8000

# 或者直接运行
python main.py
```

## API 文档

启动后访问: http://localhost:8000/docs

## 接口说明

| 接口 | 说明 | 参数 |
|------|------|------|
| `GET /api/klines/{symbol}` | K线数据 | period, start_date, end_date, limit |
| `GET /api/symbols` | 股票搜索 | q, limit |
| `GET /api/realtime/{symbol}` | 实时行情 | - |
| `GET /api/health` | 健康检查 | - |

## 示例

```bash
# 获取茅台日K
curl "http://localhost:8000/api/klines/600519?period=1d&limit=100"

# 搜索股票
curl "http://localhost:8000/api/symbols?q=茅台"

# 实时行情
curl "http://localhost:8000/api/realtime/600519"
```
