# Chainlink REST API 가격 수집

## Feed IDs

```python
CHAINLINK_FEED_IDS = {
    "btc": "0x00039d9e45394f473ab1f050a1b963e6b05351e52d71e507509ada0c95ed75b8",
    "eth": "0x000359843a543ee2fe414dc14c7e7920ef10f4372990b79d6361cdc0dd1ba782",
    "sol": "0x0003b778d3f6b2ac4991302b89cb313f99a42467d6c9c5f96f57c29c0d2bc24f",
    "xrp": "0x0003c16c6aed42294f5cb4741f6e59ba2d728f0eae2eb9e6d3f555808c59fc45",
}
```

## API 호출

```python
query = "LIVE_STREAM_REPORTS_QUERY"
variables = f'{{"feedId":"{feed_id}"}}'
url = f"https://data.chain.link/api/query-timescale?query={query}&variables={variables}"

async with session.get(url, timeout=10) as response:
    data = await response.json()
    nodes = data["data"]["liveStreamReports"]["nodes"]

    # 최신 가격
    latest_price = float(nodes[0]["price"]) / 1e18
    timestamp = datetime.fromisoformat(nodes[0]["validFromTimestamp"])
```
