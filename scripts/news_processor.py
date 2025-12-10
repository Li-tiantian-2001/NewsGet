import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta
from html import unescape
from typing import Dict, List, Optional

import requests
import yaml
from bs4 import BeautifulSoup
import feedparser


class ProgressTracker:
    steps = {
        0: "步骤 0：初始化",
        1: "步骤 1：加载新闻源",
        2: "步骤 2：爬取原始新闻",
        3: "步骤 3：正文清洗",
        4: "步骤 4：分类",
        5: "步骤 5：生成输出",
        6: "步骤 6：日志与结束",
    }

    def __init__(self, path: str = "progress.md") -> None:
        self.path = path
        self.status: Dict[int, bool] = {step: False for step in self.steps}
        self.last_error: str = "无"
        self.current: str = "未开始"

    def initialize(self) -> None:
        self.status[0] = True
        self.current = self.steps[0]
        self.write()

    def mark_running(self, step: int) -> None:
        self.current = self.steps.get(step, f"步骤 {step}")
        self.write()

    def mark_completed(self, step: int, next_step_desc: Optional[str] = None) -> None:
        self.status[step] = True
        self.current = next_step_desc or "全部步骤已完成"
        self.write()

    def set_error(self, message: str) -> None:
        self.last_error = message
        self.write()

    def write(self) -> None:
        lines = ["## 任务清单"]
        for step in range(0, 7):
            checked = "x" if self.status.get(step) else " "
            lines.append(f"- [{checked}] {self.steps[step]}")
        lines.append("")
        lines.append("## 当前状态")
        lines.append(f"- 正在执行：{self.current}")
        lines.append(f"- 最近异常：{self.last_error if self.last_error else '无'}")
        content = "\n".join(lines) + "\n"
        with open(self.path, "w", encoding="utf-8") as fp:
            fp.write(content)


def ensure_directories() -> None:
    for path in ("data", "logs"):
        os.makedirs(path, exist_ok=True)


def read_yaml_config(path: str) -> Dict:
    with open(path, "r", encoding="utf-8") as fp:
        return yaml.safe_load(fp) or {}


def check_source_access(url: str, timeout: int = 10) -> bool:
    try:
        response = requests.head(url, timeout=timeout, allow_redirects=True)
        if response.status_code < 400:
            return True
        # Some sources may not allow HEAD; fallback to GET
        response = requests.get(url, timeout=timeout, stream=True)
        return response.status_code < 400
    except requests.RequestException:
        return False


def parse_rss(url: str, time_cutoff: datetime) -> List[Dict]:
    parsed = feedparser.parse(url)
    items: List[Dict] = []
    for entry in parsed.entries:
        published_parsed = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
        if published_parsed:
            published = datetime(*published_parsed[:6])
        else:
            published = datetime.utcnow()
        if published < time_cutoff:
            continue
        items.append(
            {
                "title": entry.get("title", ""),
                "summary": entry.get("summary", ""),
                "content": entry.get("summary", ""),
                "url": entry.get("link", url),
                "timestamp": published.isoformat() + "Z",
                "source": parsed.feed.get("title", "RSS") if parsed.feed else "RSS",
            }
        )
    return items


def parse_api(url: str, headers: Optional[Dict[str, str]], time_cutoff: datetime) -> List[Dict]:
    response = requests.get(url, headers=headers, timeout=15)
    response.raise_for_status()
    payload = response.json()
    items = payload.get("articles") or payload.get("data") or payload
    results: List[Dict] = []
    if isinstance(items, dict):
        items = items.get("articles") or items.get("items") or []
    if not isinstance(items, list):
        return results
    for entry in items:
        published_raw = entry.get("publishedAt") or entry.get("date") or entry.get("timestamp")
        published = datetime.fromisoformat(published_raw.replace("Z", "+00:00")) if published_raw else datetime.utcnow()
        if published < time_cutoff:
            continue
        results.append(
            {
                "title": entry.get("title", ""),
                "summary": entry.get("description", ""),
                "content": entry.get("content") or entry.get("description", ""),
                "url": entry.get("url", url),
                "timestamp": published.isoformat().replace("+00:00", "Z"),
                "source": entry.get("source", {}).get("name") if isinstance(entry.get("source"), dict) else entry.get("source") or "API",
            }
        )
    return results


def parse_html(url: str, time_cutoff: datetime) -> List[Dict]:
    response = requests.get(url, timeout=15)
    response.raise_for_status()
    html = response.text
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.string.strip() if soup.title and soup.title.string else url
    text_fragments = []
    for tag in soup.find_all(text=True):
        if tag.parent.name in {"script", "style", "noscript"}:
            continue
        snippet = tag.strip()
        if snippet:
            text_fragments.append(snippet)
    content = "\n".join(text_fragments)
    timestamp = datetime.utcnow().isoformat() + "Z"
    if datetime.fromisoformat(timestamp.replace("Z", "+00:00")) < time_cutoff:
        return []
    return [
        {
            "title": title,
            "summary": content[:280],
            "content": content,
            "url": url,
            "timestamp": timestamp,
            "source": url,
        }
    ]


def clean_text(text: str) -> str:
    text = unescape(text)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)


def clean_news_items(items: List[Dict]) -> List[Dict]:
    cleaned: List[Dict] = []
    for item in items:
        cleaned.append(
            {
                **item,
                "title": clean_text(item.get("title", "")),
                "summary": clean_text(item.get("summary", "")),
                "content": clean_text(item.get("content", "")),
            }
        )
    return cleaned


CATEGORY_KEYWORDS = {
    "国际": ["国际", "world", "global", "联合国", "外交"],
    "商业": ["商业", "经济", "finance", "market", "公司", "投资"],
    "科技": ["科技", "AI", "人工智能", "tech", "软件", "芯片"],
    "娱乐": ["娱乐", "电影", "音乐", "明星", "综艺"],
    "体育": ["体育", "比赛", "足球", "篮球", "奥运"],
    "社会": ["社会", "民生", "事故", "法院", "教育"],
    "搞笑": ["搞笑", "离奇", "趣闻", "funny", "奇葩"],
    "本地新闻": ["本地", "社区", "市政", "县", "街道"],
}


def classify_item(item: Dict) -> str:
    text = f"{item.get('title', '')} {item.get('summary', '')}".lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        for keyword in keywords:
            if keyword.lower() in text:
                return category
    return "社会"


def classify_news(items: List[Dict]) -> List[Dict]:
    classified = []
    for item in items:
        category = classify_item(item)
        classified.append({**item, "category": category})
    return classified


def build_digest(items: List[Dict]) -> str:
    lines = ["# 每日新闻摘要", ""]
    if not items:
        lines.append("今日暂无符合条件的新闻。")
        return "\n".join(lines) + "\n"

    lines.append("## 重点新闻")
    for item in items[:10]:
        lines.append(f"- ({item.get('category', '未分类')}) {item.get('title', '')} — {item.get('summary', '')[:120]}")
    lines.append("")

    lines.append("## 搞笑 / 离奇")
    funny_items = [i for i in items if i.get("category") == "搞笑"]
    if funny_items:
        for item in funny_items[:5]:
            lines.append(f"- {item.get('title', '')} — {item.get('summary', '')[:120]}")
    else:
        lines.append("- 暂无搞笑新闻")
    lines.append("")

    lines.append("## 趋势与分类分布")
    category_count: Dict[str, int] = {}
    for item in items:
        cat = item.get("category", "未分类")
        category_count[cat] = category_count.get(cat, 0) + 1
    for category, count in sorted(category_count.items(), key=lambda c: c[0]):
        lines.append(f"- {category}: {count} 篇")
    lines.append("")

    lines.append("> 由自动脚本生成，可作为视频或播客脚本草稿。")
    return "\n".join(lines) + "\n"


def write_json(path: str, data: object) -> None:
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)


def load_sources(config_path: str, logger: logging.Logger) -> Dict[str, List[Dict]]:
    config = read_yaml_config(config_path)
    sources: Dict[str, List[Dict]] = {"rss": [], "api": [], "html": []}
    for key in sources:
        entries = config.get(key, []) or []
        for entry in entries:
            url = entry.get("url")
            if not url:
                logger.warning("配置项缺少 URL：%s", entry)
                continue
            accessible = check_source_access(url)
            if not accessible:
                logger.warning("源不可访问，已跳过：%s", url)
                continue
            sources[key].append(entry)
    return sources


def fetch_sources(sources: Dict[str, List[Dict]], time_cutoff: datetime, logger: logging.Logger) -> List[Dict]:
    collected: List[Dict] = []
    for rss in sources.get("rss", []):
        logger.info("拉取 RSS：%s", rss.get("url"))
        collected.extend(parse_rss(rss["url"], time_cutoff))
    for api_source in sources.get("api", []):
        logger.info("调用 API：%s", api_source.get("url"))
        headers = api_source.get("headers") if isinstance(api_source.get("headers"), dict) else None
        try:
            collected.extend(parse_api(api_source["url"], headers, time_cutoff))
        except Exception as exc:  # noqa: BLE001
            logger.warning("API 源失败，已跳过 %s：%s", api_source.get("url"), exc)
    for html_source in sources.get("html", []):
        logger.info("抓取 HTML：%s", html_source.get("url"))
        try:
            collected.extend(parse_html(html_source["url"], time_cutoff))
        except Exception as exc:  # noqa: BLE001
            logger.warning("HTML 源失败，已跳过 %s：%s", html_source.get("url"), exc)
    return collected


def configure_logger(log_path: str) -> logging.Logger:
    logger = logging.getLogger("news_processor")
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(formatter)
    if not logger.handlers:
        logger.addHandler(fh)
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(formatter)
        logger.addHandler(sh)
    return logger


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="每日新闻爬取脚本")
    parser.add_argument("--hours", type=int, default=24, help="抓取时间窗口（小时）")
    parser.add_argument("--sources", default="config/news_sources.yaml", help="新闻源配置文件")
    parser.add_argument("--prompts", default="config/news_prompts.yaml", help="自定义 Prompt 文件（可选）")
    parser.add_argument("--progress", default="progress.md", help="进度文件路径")
    parser.add_argument("--raw", default="data/raw_news.tmp.json", help="原始新闻输出")
    parser.add_argument("--clean", default="data/clean_news.json", help="清洗后新闻输出")
    parser.add_argument("--daily", default="data/news_daily.json", help="结构化新闻输出")
    parser.add_argument("--digest", default="data/news_digest.md", help="每日摘要输出")
    parser.add_argument("--log", default="logs/processor.log", help="日志文件")
    return parser.parse_args()


def main() -> None:
    args = parse_arguments()
    ensure_directories()
    tracker = ProgressTracker(args.progress)
    tracker.initialize()

    logger = configure_logger(args.log)
    start_time = datetime.utcnow()
    time_cutoff = start_time - timedelta(hours=args.hours)

    try:
        tracker.mark_running(1)
        sources = load_sources(args.sources, logger)
        tracker.mark_completed(1, "步骤 2：爬取原始新闻")

        tracker.mark_running(2)
        raw_items = fetch_sources(sources, time_cutoff, logger)
        write_json(args.raw, raw_items)
        tracker.mark_completed(2, "步骤 3：正文清洗")

        tracker.mark_running(3)
        cleaned_items = clean_news_items(raw_items)
        write_json(args.clean, cleaned_items)
        tracker.mark_completed(3, "步骤 4：分类")

        tracker.mark_running(4)
        classified_items = classify_news(cleaned_items)
        tracker.mark_completed(4, "步骤 5：生成输出")

        tracker.mark_running(5)
        write_json(args.daily, classified_items)
        digest = build_digest(classified_items)
        with open(args.digest, "w", encoding="utf-8") as fp:
            fp.write(digest)
        tracker.mark_completed(5, "步骤 6：日志与结束")

        tracker.mark_running(6)
        elapsed = datetime.utcnow() - start_time
        logger.info(
            "处理完成，共收集 %s 条新闻，耗时 %.2f 秒。",
            len(classified_items),
            elapsed.total_seconds(),
        )
        tracker.mark_completed(6, "全部步骤已完成")
    except Exception as exc:  # noqa: BLE001
        tracker.set_error(str(exc))
        logger.exception("处理失败：%s", exc)
        raise


if __name__ == "__main__":
    main()
