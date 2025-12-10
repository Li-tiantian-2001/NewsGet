# NewsGet

每日新闻爬取与摘要脚本，实现从 RSS/API/HTML 源抓取新闻、清洗正文、分类并输出结构化文件与摘要。

## 快速开始
1. 安装依赖
   ```bash
   pip install -r requirements.txt
   ```
2. 配置新闻源
   编辑 `config/news_sources.yaml`，填写 RSS、API、HTML 源及必要的请求头。
3. 运行脚本（默认抓取过去 24 小时）
   ```bash
   python scripts/news_processor.py --hours 24
   ```

## 配置文件
- `config/news_sources.yaml`：新闻源列表（RSS/API/HTML）。脚本会自动跳过不可访问的源。
- `config/news_prompts.yaml`：可选的 AI 分类与摘要 Prompt 模板。

## 输出文件
运行脚本后会生成：
- `data/raw_news.tmp.json`：拉取的原始新闻（含正文或摘要）。
- `data/clean_news.json`：清洗去噪后的新闻。
- `data/news_daily.json`：结构化的最终新闻数据（含分类）。
- `data/news_digest.md`：AI 摘要草稿，含重点与搞笑新闻板块。
- `logs/processor.log`：日志记录。
- `progress.md`：任务进度，按步骤自动更新。

## 进度文件格式（示例）
```
## 任务清单
- [x] 步骤 0：初始化
- [ ] 步骤 1：加载新闻源
- [ ] 步骤 2：爬取原始新闻
- [ ] 步骤 3：正文清洗
- [ ] 步骤 4：分类
- [ ] 步骤 5：生成输出
- [ ] 步骤 6：日志与结束

## 当前状态
- 正在执行：步骤 X（描述具体内容…）
- 最近异常：无 / 错误详情
```

## 常用参数
- `--hours`：抓取时间窗口（小时），默认 24。
- `--sources`：新闻源配置文件路径。
- `--progress`：进度文件路径。
- `--raw` / `--clean` / `--daily` / `--digest`：输出文件路径。
- `--log`：日志文件路径。

## 注意事项
- HTML 正文抽取采用简单文本去噪，如需更精准可替换为 readability 等工具。
- 分类采用关键词规则，若结合大模型可在分类步骤引入 `news_prompts.yaml`。
- 默认进度文件已完成“步骤 0：初始化”，脚本运行过程中会逐步更新后续步骤状态。
