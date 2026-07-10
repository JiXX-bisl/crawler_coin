# Bitcoin Protocol Static Crawler

用于第一阶段“静态网页爬取”：采集比特币协议知识库来源，包括 HTML、PDF 和 GitHub BIPs，并统一输出 JSONL、XLSX、SQLite 和清洗文本文件。

## 安装依赖

```powershell
pip install requests beautifulsoup4 lxml pandas openpyxl pypdf -i https://mirrors.aliyun.com/pypi/simple
```

其中 `pypdf` 用于从 `bitcoin.pdf` 提取文本；如果不安装，程序仍会下载 PDF，但无法提取 PDF 正文。

## 执行

0. 测试网络环境是否能够正确爬取数据 
```powershell
python scripts/virtual_currency_static_crawler.py --config configs/virtual_currency_seed_urls.json --out data/virtual_currency_raw --dry-run
```
1. 根据爬取网址配置文件`configs/virtual_currency_seed_urls.json`进行内容获取
```powershell
# 完整爬取虚拟货币
python scripts/virtual_currency_static_crawler.py --config configs/virtual_currency_seed_urls.json --out data/virtual_currency_raw --delay 1.0 --timeout 60
# 只爬取A优先级（B、C同理）
python scripts/virtual_currency_static_crawler.py --config configs/virtual_currency_seed_urls.json --out data/virtual_currency_raw_A --only-priority A --delay 1.0 --timeout 60
# 只爬取固定币种
python scripts/virtual_currency_static_crawler.py --config configs/virtual_currency_seed_urls.json --out data/virtual_currency_raw_btc_eth --only-coin BTC,ETH --delay 1.0 --timeout 60
# 完整爬取违法行为案例
python scripts/virtual_currency_illegal_case_crawler.py
```
2. 对爬取得到的结果进行过滤（可选）
```powershell
python scripts/filter_records_jsonl.py "地址" --out-dir "地址"
```
3. chunk
```powershell
# chunk
python scripts/chunk_records.py --input data/virtual_currency_filtered/records_filtered.jsonl --out data/virtual_currency_chunks --mode auto --min-chunk-chars 300
# to txt (optional)
python scripts/split_chunks_for_dify_txt.py --input data/virtual_currency_chunks/chunks.jsonl --out data/dify_txt --max-file-mb 14
```

## 注意

- 如果有VPN，需要在执行时挂载对应端口。
```powershell
$env:HTTP_PROXY="http://127.0.0.1:7892"
$env:HTTPS_PROXY="http://127.0.0.1:7892"
```
