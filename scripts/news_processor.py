import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import urljoin

import feedparser
import requests
import yaml
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter, Retry


NEWS_CATEGORIES = [
    "国际",
    "商业",
    "科技",
    "娱乐",
    "体育",
    "社会",
    "搞笑",
    "猎奇",
]

DEFAULT_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0",
]


@dataclass
class CategoryConfig:
    url: str
    type: str = "rss"  # rss | html
    list_selector: Optional[str] = None
    title_selector: Optional[str] = None
    link_selector: Optional[str] = None
    summary_selector: Optional[str] = None
    time_selector: Optional[str] = None
    time_attr: Optional[str] = None
    supports_24h_filter: bool = False


@dataclass
class SiteConfig:
    name: str
    categories: Dict[str, CategoryConfig]


class ProgressTracker:
    steps = {
        0: "步骤 0：初始化进度",
        1: "步骤 1：加载站点配置",
        2: "步骤 2：构建请求会话",
        3: "步骤 3：抓取站点新闻",
        4: "步骤 4：处理一句话新闻",
        5: "步骤 5：构建输出文件内容",
        6: "步骤 6：写入 txt 文件",
        7: "步骤 7：清理临时文件",
    }

    def __init__(self, path: str, run_index: int, target_date: str) -> None:
        self.path = path
        self.status: Dict[int, bool] = {step: False for step in self.steps}
        self.current: str = "未开始"
        self.run_index = run_index
        self.target_date = target_date

    def initialize(self) -> None:
        self.status[0] = True
        self.current = self.steps[0]
        self.write()

    def mark_running(self, step: int, detail: Optional[str] = None) -> None:
        desc = self.steps.get(step, f"步骤 {step}")
        if detail:
            desc = f"{desc} - {detail}"
        self.current = desc
        self.write()

    def mark_completed(self, step: int, next_step_desc: Optional[str] = None) -> None:
        self.status[step] = True
        self.current = next_step_desc or self.steps.get(step, "")
        self.write()

    def write(self) -> None:
        lines = ["## 任务清单"]
        for step in range(0, 8):
            checked = "x" if self.status.get(step) else " "
            lines.append(f"- [{checked}] {self.steps[step]}")
        lines.append("")
        lines.append("## 当前状态")
        lines.append(f"正在执行：{self.current}")
        lines.append(f"当前运行编号：{self.run_index}")
        lines.append(f"目标日期：{self.target_date}")
        content = "\n".join(lines) + "\n"
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as fp:
            fp.write(content)


def load_user_agents(path: Optional[str]) -> List[str]:
    if not path:
        return DEFAULT_USER_AGENTS
    if not os.path.exists(path):
        return DEFAULT_USER_AGENTS
    with open(path, "r", encoding="utf-8") as fp:
        lines = [line.strip() for line in fp.readlines() if line.strip()]
    return lines or DEFAULT_USER_AGENTS


def build_session(
    user_agents: List[str], proxy: Optional[str], timeout: int, max_retries: int, retry_interval_seconds: int
) -> requests.Session:
    session = requests.Session()
    adapter = HTTPAdapter(
        max_retries=Retry(
            total=max_retries,
            backoff_factor=retry_interval_seconds,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET"],
        )
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": user_agents[0]})
    if proxy:
        session.proxies.update({"http": proxy, "https": proxy})
    session.request_timeout = timeout  # type: ignore[attr-defined]
    return session


def parse_timestamp(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        try:
            parsed = parsedate_to_datetime(raw)
            return parsed.astimezone(timezone.utc)
        except (TypeError, ValueError, OverflowError):
            return None


def default_sites() -> List[SiteConfig]:
    return [
        SiteConfig(
            name="BBC",
            categories={
                "国际": CategoryConfig(url="https://feeds.bbci.co.uk/news/world/rss.xml", type="rss", supports_24h_filter=False),
                "商业": CategoryConfig(url="https://feeds.bbci.co.uk/news/business/rss.xml", type="rss", supports_24h_filter=False),
                "科技": CategoryConfig(url="https://feeds.bbci.co.uk/news/technology/rss.xml", type="rss", supports_24h_filter=False),
                "娱乐": CategoryConfig(url="https://feeds.bbci.co.uk/news/entertainment_and_arts/rss.xml", type="rss", supports_24h_filter=False),
                "体育": CategoryConfig(url="https://feeds.bbci.co.uk/sport/rss.xml", type="rss", supports_24h_filter=False),
            },
        ),
        SiteConfig(
            name="CNN",
            categories={
                "国际": CategoryConfig(url="http://rss.cnn.com/rss/edition_world.rss", type="rss"),
                "商业": CategoryConfig(url="http://rss.cnn.com/rss/money_latest.rss", type="rss"),
                "科技": CategoryConfig(url="http://rss.cnn.com/rss/edition_technology.rss", type="rss"),
                "娱乐": CategoryConfig(url="http://rss.cnn.com/rss/edition_entertainment.rss", type="rss"),
                "体育": CategoryConfig(url="http://rss.cnn.com/rss/edition_sport.rss", type="rss"),
            },
        ),
        SiteConfig(
            name="Reuters",
            categories={
                "国际": CategoryConfig(url="https://feeds.reuters.com/Reuters/worldNews", type="rss"),
                "商业": CategoryConfig(url="https://feeds.reuters.com/reuters/businessNews", type="rss"),
                "科技": CategoryConfig(url="https://feeds.reuters.com/reuters/technologyNews", type="rss"),
                "体育": CategoryConfig(url="https://feeds.reuters.com/reuters/sportsNews", type="rss"),
            },
        ),
        SiteConfig(
            name="Yahoo",
            categories={
                "国际": CategoryConfig(url="https://news.yahoo.com/rss/world", type="rss"),
                "商业": CategoryConfig(url="https://news.yahoo.com/rss/business", type="rss"),
                "科技": CategoryConfig(url="https://news.yahoo.com/rss/tech", type="rss"),
                "娱乐": CategoryConfig(url="https://news.yahoo.com/rss/entertainment", type="rss"),
                "体育": CategoryConfig(url="https://sports.yahoo.com/rss/", type="rss"),
            },
        ),
        SiteConfig(
            name="AP",
            categories={
                "国际": CategoryConfig(url="https://apnews.com/hub/ap-top-news?output=rss", type="rss"),
                "商业": CategoryConfig(url="https://apnews.com/hub/business?output=rss", type="rss"),
                "科技": CategoryConfig(url="https://apnews.com/hub/technology?output=rss", type="rss"),
                "娱乐": CategoryConfig(url="https://apnews.com/hub/entertainment?output=rss", type="rss"),
                "体育": CategoryConfig(url="https://apnews.com/hub/sports?output=rss", type="rss"),
            },
        ),
        SiteConfig(
            name="TechCrunch",
            categories={
                "科技": CategoryConfig(url="https://techcrunch.com/feed/", type="rss"),
            },
        ),
    ]


def parse_site_config_from_yaml(path: str) -> List[SiteConfig]:
    with open(path, "r", encoding="utf-8") as fp:
        config = yaml.safe_load(fp) or {}
    sites: List[SiteConfig] = []
    for site_entry in config.get("sites", []):
        name = site_entry.get("name")
        cat_entries = site_entry.get("categories", {})
        categories: Dict[str, CategoryConfig] = {}
        for cat_name, cat_cfg in cat_entries.items():
            if cat_name not in NEWS_CATEGORIES:
                continue
            categories[cat_name] = CategoryConfig(
                url=cat_cfg.get("url"),
                type=cat_cfg.get("type", "rss"),
                list_selector=cat_cfg.get("list_selector"),
                title_selector=cat_cfg.get("title_selector"),
                link_selector=cat_cfg.get("link_selector"),
                summary_selector=cat_cfg.get("summary_selector"),
                time_selector=cat_cfg.get("time_selector"),
                time_attr=cat_cfg.get("time_attr"),
                supports_24h_filter=bool(cat_cfg.get("supports_24h_filter", False)),
            )
        if name and categories:
            sites.append(SiteConfig(name=name, categories=categories))
    return sites or default_sites()


def check_site_access(session: requests.Session, url: str, timeout: int) -> bool:
    try:
        resp = session.head(url, timeout=timeout, allow_redirects=True)
        if resp.status_code < 400:
            return True
    except requests.RequestException:
        pass
    try:
        resp = session.get(url, timeout=timeout, stream=True)
        return resp.status_code < 400
    except requests.RequestException:
        return False


def parse_rss_category(url: str, time_cutoff: datetime, session: requests.Session) -> List[Dict]:
    # feedparser handles fetching; session not used directly but kept for parity
    parsed = feedparser.parse(url)
    items: List[Dict] = []
    for entry in parsed.entries:
        published_parsed = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
        published = None
        if published_parsed:
            published = datetime(*published_parsed[:6], tzinfo=timezone.utc)
        else:
            published = parse_timestamp(entry.get("published") or entry.get("updated"))
        if not published:
            published = datetime.now(timezone.utc)
        if published < time_cutoff:
            continue
        items.append(
            {
                "title": entry.get("title", ""),
                "summary": entry.get("summary", ""),
                "content": entry.get("summary", ""),
                "url": entry.get("link", url),
                "timestamp": published.isoformat(),
            }
        )
    return items


def fetch_article_summary(session: requests.Session, url: str, timeout: int) -> str:
    try:
        resp = session.get(url, timeout=timeout)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        paragraph = soup.find("p")
        if paragraph:
            return paragraph.get_text(strip=True)
        return soup.get_text(" ", strip=True)[:280]
    except requests.RequestException:
        return ""


def parse_html_category(config: CategoryConfig, session: requests.Session, time_cutoff: datetime, timeout: int) -> List[Dict]:
    resp = session.get(config.url, timeout=timeout)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    elements: Iterable = soup.select(config.list_selector) if config.list_selector else soup.find_all("article")
    items: List[Dict] = []
    for element in elements:
        title_elem = element.select_one(config.title_selector) if config.title_selector else element.find("a")
        link_elem = element.select_one(config.link_selector) if config.link_selector else title_elem
        summary_elem = element.select_one(config.summary_selector) if config.summary_selector else None
        if not title_elem or not link_elem:
            continue
        title_text = title_elem.get_text(strip=True)
        link = link_elem.get("href") or ""
        link = urljoin(config.url, link)
        summary_text = summary_elem.get_text(strip=True) if summary_elem else ""
        time_elem = element.select_one(config.time_selector) if config.time_selector else None
        timestamp_raw = None
        if time_elem:
            timestamp_raw = time_elem.get(config.time_attr) if config.time_attr else time_elem.get_text(strip=True)
        published = parse_timestamp(timestamp_raw) or datetime.now(timezone.utc)
        if not config.supports_24h_filter and published < time_cutoff:
            continue
        if not summary_text:
            summary_text = fetch_article_summary(session, link, timeout)
        items.append(
            {
                "title": title_text,
                "summary": summary_text,
                "content": summary_text,
                "url": link,
                "timestamp": published.isoformat(),
            }
        )
    return items


def trim_one_sentence(item: Dict) -> Tuple[str, bool]:
    text = item.get("summary") or item.get("content") or ""
    text = text.replace("\n", " ").strip()
    if not text:
        return "", False
    one_line = text[:120]
    return one_line, True


def build_output_text(news_data: Dict[str, Dict[str, List[Dict]]]) -> str:
    lines: List[str] = []
    for site, categories in news_data.items():
        lines.append(f"{site}：")
        for category, items in categories.items():
            lines.append(f"  {category}：")
            for idx, item in enumerate(items, start=1):
                lines.append("    %d. {" % idx)
                lines.append(f"         标题：{item.get('title', '')}")
                lines.append(f"         内容：{item.get('one_liner', '')}")
                lines.append(f"         来源链接：{item.get('url', '')}")
                lines.append("       }")
            if not items:
                lines.append("    - 暂无符合条件的新闻")
    return "\n".join(lines) + "\n"


def write_json(path: str, data: object) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)


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


def collect_news_for_category(
    site: SiteConfig,
    category_name: str,
    category_config: CategoryConfig,
    session: requests.Session,
    time_cutoff: datetime,
    timeout: int,
) -> List[Dict]:
    if category_config.type == "rss":
        return parse_rss_category(category_config.url, time_cutoff, session)
    if category_config.type == "html":
        return parse_html_category(category_config, session, time_cutoff, timeout)
    return []


def clean_and_limit(items: List[Dict], limit: int) -> List[Dict]:
    cleaned: List[Dict] = []
    for item in items:
        cleaned.append(
            {
                "title": item.get("title", "").strip(),
                "summary": (item.get("summary") or "").strip(),
                "content": (item.get("content") or "").strip(),
                "url": item.get("url", ""),
                "timestamp": item.get("timestamp", ""),
            }
        )
    return cleaned[:limit]


def remove_temp_files(temp_files: List[str]) -> None:
    for path in temp_files:
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                continue


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="多站点新闻爬取脚本")
    parser.add_argument("--sites", default="config/sites.yaml", help="站点配置文件路径，默认使用内置配置")
    parser.add_argument("--output-dir", default="data/output", help="输出目录")
    parser.add_argument("--run-index", type=int, default=1, help="当日执行编号")
    parser.add_argument("--date", dest="target_date", default=datetime.now().strftime("%Y-%m-%d"), help="目标日期")
    parser.add_argument("--max-retries", type=int, default=2, help="请求重试次数")
    parser.add_argument("--retry-interval-seconds", type=int, default=2, help="重试间隔秒数")
    parser.add_argument("--timeout", type=int, default=15, help="请求超时秒数")
    parser.add_argument("--proxy", default=None, help="代理地址，可选")
    parser.add_argument("--user-agents", default=None, help="自定义 User-Agent 文件，一行一个")
    parser.add_argument("--progress", default=None, help="自定义 progress.md 路径（可选）")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    os.makedirs(args.output_dir, exist_ok=True)
    progress_path = args.progress or os.path.join(args.output_dir, "progress.md")
    tracker = ProgressTracker(progress_path, args.run_index, args.target_date)
    tracker.initialize()

    log_path = os.path.join(args.output_dir, "news_processor.log")
    logger = configure_logger(log_path)
    time_cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    temp_files: List[str] = []

    try:
        tracker.mark_running(1)
        if os.path.exists(args.sites):
            sites = parse_site_config_from_yaml(args.sites)
        else:
            sites = default_sites()
        tracker.mark_completed(1, "步骤 2：构建请求会话")

        tracker.mark_running(2)
        user_agents = load_user_agents(args.user_agents)
        session = build_session(user_agents, args.proxy, args.timeout, args.max_retries, args.retry_interval_seconds)
        accessible_sites: List[SiteConfig] = []
        for site in sites:
            site_ok = True
            for cat in site.categories.values():
                if not check_site_access(session, cat.url, args.timeout):
                    logger.warning("站点不可访问，已跳过：%s", cat.url)
                    site_ok = False
                    break
            if site_ok:
                accessible_sites.append(site)
        tracker.mark_completed(2, "步骤 3：抓取站点新闻")

        tracker.mark_running(3)
        news_data: Dict[str, Dict[str, List[Dict]]] = {}
        for site in accessible_sites:
            site_bucket: Dict[str, List[Dict]] = {}
            for category_name, category_cfg in site.categories.items():
                items = collect_news_for_category(site, category_name, category_cfg, session, time_cutoff, args.timeout)
                filtered = [i for i in items if parse_timestamp(i.get("timestamp")) or datetime.now(timezone.utc) >= time_cutoff]
                limited = clean_and_limit(filtered, 20)
                site_bucket[category_name] = limited
                tracker.mark_running(3, f"已完成 {site.name} - {category_name}")
            news_data[site.name] = site_bucket
            tracker.mark_running(3, f"已完成 {site.name}")
        tracker.mark_completed(3, "步骤 4：处理一句话新闻")

        tracker.mark_running(4)
        for site, categories in news_data.items():
            for category_name, items in categories.items():
                processed_items: List[Dict] = []
                for item in items:
                    one_line, ok = trim_one_sentence(item)
                    if not ok:
                        logger.warning("未能提取一句话新闻：%s", item.get("url"))
                    processed_items.append({**item, "one_liner": one_line})
                categories[category_name] = processed_items
                tracker.mark_running(4, f"已处理 {site} - {category_name}")
        tracker.mark_completed(4, "步骤 5：构建输出文件内容")

        tracker.mark_running(5)
        output_text = build_output_text(news_data)
        tracker.mark_completed(5, "步骤 6：写入 txt 文件")

        tracker.mark_running(6)
        output_filename = f"{args.target_date}新闻第{args.run_index}次.txt"
        output_path = os.path.join(args.output_dir, output_filename)
        with open(output_path, "w", encoding="utf-8") as fp:
            fp.write(output_text)
        summary_json_path = os.path.join(args.output_dir, "news_summary.json")
        write_json(summary_json_path, news_data)
        tracker.mark_completed(6, "步骤 7：清理临时文件")

        tracker.mark_running(7)
        remove_temp_files(temp_files)
        tracker.mark_completed(7, "全部步骤已完成")
        logger.info("任务完成，输出文件：%s", output_path)
    except Exception as exc:  # noqa: BLE001
        logger.exception("执行失败：%s", exc)
        tracker.mark_running(-1, "执行失败")
        raise


if __name__ == "__main__":
    main()
