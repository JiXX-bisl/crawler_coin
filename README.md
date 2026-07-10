# Unified Crawler

统一爬虫使用同一个入口，根据不同配置文件执行不同采集任务。

## 配置

- `configs/coin_knowledge.json`：虚拟货币知识资料采集配置
- `configs/illegal_cases.json`：虚拟货币违法犯罪案例采集配置

两个配置文件均为完整自包含结构，包含 `job`、`runtime`、`output` 和 `sources`。业务字段仅保存在 `metadata.knowledge` 或 `metadata.illegal_case` 中，核心爬虫不解析业务语义。

## 运行

```powershell
python main.py --config configs/coin_knowledge.json
python main.py --config configs/illegal_cases.json
```

常用参数：

```powershell
python main.py --config configs/illegal_cases.json --source-id source_001 --max-pages 1 --max-depth 0
python main.py --config configs/coin_knowledge.json --dry-run
```

可选参数：`--source-id`、`--max-pages`、`--max-depth`、`--output-dir`、`--dry-run`、`--no-progress`。

## 代理端口

如果需要通过 VPN 或本地代理访问外部网站，保留原先的端口配置：

```powershell
$env:HTTP_PROXY="http://127.0.0.1:7892"
$env:HTTPS_PROXY="http://127.0.0.1:7892"
```

设置后再执行 `python main.py --config ...`。

## 输出

默认写入配置中的 `output.directory`，包含：

- `records.jsonl`
- `failures.jsonl`
- `crawl_report.json`

`data/` 已由 `.gitignore` 排除，不提交爬取结果。
