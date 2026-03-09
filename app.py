import json
import os
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from pathlib import Path
from threading import Lock
from typing import Dict, List, Optional

import feedparser
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from flask import Flask, redirect, render_template, request, url_for
from openai import OpenAI

load_dotenv()

app = Flask(__name__)

# 常用 AI 资讯 RSS 源，可按需扩展
RSS_SOURCES = [
    "https://openai.com/blog/rss.xml",
    "https://www.anthropic.com/news/rss.xml",
    "https://ai.googleblog.com/feeds/posts/default?alt=rss",
    "https://huggingface.co/blog/feed.xml",
    "https://www.technologyreview.com/topic/artificial-intelligence/feed",
    "https://venturebeat.com/category/ai/feed/",
]

MAX_ITEMS = 10
MIN_ITEMS = 5
CN_TZ = timezone(timedelta(hours=8))
DATA_PATH = Path("data/summaries.json")
LOCK = Lock()

# 基础来源权重（越高越优先）
SOURCE_WEIGHTS = {
    "OpenAI": 4,
    "Anthropic": 4,
    "Google AI Blog": 4,
    "MIT Technology Review": 3,
    "Hugging Face": 2,
}

# 简单内存缓存，避免每次刷新都重复调用模型
CACHE: Dict[str, List[Dict[str, str]]] = {}


def ensure_data_file() -> None:
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not DATA_PATH.exists():
        DATA_PATH.write_text("{}", encoding="utf-8")


def load_persisted_cache() -> None:
    ensure_data_file()
    try:
        persisted = json.loads(DATA_PATH.read_text(encoding="utf-8"))
        if isinstance(persisted, dict):
            for day_key, items in persisted.items():
                if isinstance(day_key, str) and isinstance(items, list):
                    CACHE[day_key] = items
    except (json.JSONDecodeError, OSError):
        pass


def persist_cache() -> None:
    ensure_data_file()
    try:
        DATA_PATH.write_text(
            json.dumps(CACHE, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass


def parse_entry_time(entry) -> datetime:
    """解析 RSS 条目时间，失败时回退到当前时间。"""
    candidate = (
        entry.get("published")
        or entry.get("updated")
        or entry.get("pubDate")
        or entry.get("created")
    )
    if not candidate:
        return datetime.now(timezone.utc)

    try:
        dt = parsedate_to_datetime(candidate)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)


def normalize_title(title: str) -> str:
    cleaned = "".join(ch.lower() for ch in title if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")
    return cleaned


def is_similar_title(title: str, seen_titles: List[str], threshold: float = 0.82) -> bool:
    current = normalize_title(title)
    if not current:
        return False
    for existing in seen_titles:
        ratio = SequenceMatcher(None, current, existing).ratio()
        if ratio >= threshold:
            return True
    return False


def fetch_latest_ai_articles(limit: int = 50) -> List[Dict[str, str]]:
    """抓取最新 AI 资讯并做 URL + 相似标题去重。"""
    articles: List[Dict[str, str]] = []

    for source in RSS_SOURCES:
        feed = feedparser.parse(source)
        source_title = feed.feed.get("title", source)

        for entry in feed.entries:
            published = parse_entry_time(entry)
            summary = (entry.get("summary") or entry.get("description") or "").strip()
            articles.append(
                {
                    "title": entry.get("title", "无标题"),
                    "url": entry.get("link", "#"),
                    "source": source_title,
                    "published": published,
                    "raw_summary": summary,
                }
            )

    # 1) URL 去重
    dedup_by_url: Dict[str, Dict[str, str]] = {}
    for item in articles:
        dedup_by_url[item["url"]] = item

    # 2) 相似标题去重（保留发布时间更近/分数更高者由后续排序决定）
    by_time = sorted(dedup_by_url.values(), key=lambda x: x["published"], reverse=True)
    final_items: List[Dict[str, str]] = []
    seen_titles: List[str] = []
    for item in by_time:
        title = item.get("title", "")
        if is_similar_title(title, seen_titles):
            continue
        seen_titles.append(normalize_title(title))
        final_items.append(item)

    return final_items[:limit]


def summarize_in_chinese(article: Dict[str, str], client: Optional[OpenAI]) -> str:
    """使用 GPT 生成中文摘要；若不可用则回退到原文简介截断。"""
    fallback = article["raw_summary"][:280] or "该资讯暂无可用摘要，请点击查看原文。"

    if client is None:
        return fallback

    prompt = (
        "请把下面的 AI 资讯内容总结成中文，突出技术亮点或实践启发。"
        "要求：不超过300字，语言精炼，可读性强。\n\n"
        f"标题：{article['title']}\n"
        f"来源：{article['source']}\n"
        f"内容：{article['raw_summary'][:2000]}"
    )

    try:
        resp = client.responses.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            input=prompt,
            temperature=0.3,
            max_output_tokens=380,
        )
        text = (resp.output_text or "").strip()
        return text[:300] if text else fallback
    except Exception:
        return fallback


def get_source_weight(source: str) -> int:
    lower = source.lower()
    for key, weight in SOURCE_WEIGHTS.items():
        if key.lower() in lower:
            return weight
    return 0


def score_article(article: Dict[str, str]) -> int:
    """按关键词、时效和来源权重给资讯打分。"""
    text = f"{article['title']} {article['raw_summary']}".lower()
    score = 0

    high_impact_keywords = [
        "release",
        "launched",
        "model",
        "benchmark",
        "paper",
        "open-source",
        "api",
        "agent",
        "multimodal",
        "reasoning",
        "sota",
        "breakthrough",
    ]

    for kw in high_impact_keywords:
        if kw in text:
            score += 2

    age_hours = (datetime.now(timezone.utc) - article["published"]).total_seconds() / 3600
    if age_hours <= 24:
        score += 3
    elif age_hours <= 72:
        score += 1

    score += get_source_weight(article.get("source", ""))
    return score


def build_daily_digest(force_refresh: bool = False) -> List[Dict[str, str]]:
    """构建当天资讯摘要列表（5~10 条），并持久化到本地文件。"""
    day_key = datetime.now(CN_TZ).strftime("%Y-%m-%d")
    if not force_refresh and day_key in CACHE:
        return CACHE[day_key]

    api_key = os.getenv("OPENAI_API_KEY")
    client = OpenAI(api_key=api_key) if api_key else None

    candidates = fetch_latest_ai_articles(limit=50)
    ranked = sorted(candidates, key=score_article, reverse=True)

    target_count = min(MAX_ITEMS, max(MIN_ITEMS, len(ranked)))
    selected = ranked[:target_count]

    result = []
    for item in selected:
        result.append(
            {
                "title": item["title"],
                "url": item["url"],
                "source": item["source"],
                "published": item["published"].astimezone(CN_TZ).strftime("%Y-%m-%d %H:%M"),
                "summary": summarize_in_chinese(item, client),
            }
        )

    with LOCK:
        CACHE[day_key] = result
        # 仅保留近 7 天，控制文件大小
        recent_keys = sorted(CACHE.keys(), reverse=True)[:7]
        for k in list(CACHE.keys()):
            if k not in recent_keys:
                CACHE.pop(k, None)
        persist_cache()

    return result


def scheduled_daily_refresh() -> None:
    """每天 08:00 自动刷新当天资讯。"""
    build_daily_digest(force_refresh=True)


@app.route("/")
def index():
    items = build_daily_digest()
    return render_template(
        "index.html",
        items=items,
        updated_at=datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M"),
    )


@app.route("/refresh", methods=["POST"])
def refresh_news():
    """手动刷新当天资讯缓存并回到首页。"""
    _ = request.form.get("source", "all")
    build_daily_digest(force_refresh=True)
    return redirect(url_for("index"))


def start_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone=CN_TZ)
    scheduler.add_job(
        scheduled_daily_refresh,
        trigger="cron",
        hour=8,
        minute=0,
        id="daily_8am_refresh",
        replace_existing=True,
    )
    scheduler.start()
    return scheduler


def should_start_scheduler() -> bool:
    debug_mode = os.getenv("FLASK_DEBUG", "true").lower() in {"1", "true", "yes"}
    if not debug_mode:
        return True
    return os.getenv("WERKZEUG_RUN_MAIN") == "true"


load_persisted_cache()
SCHEDULER = start_scheduler() if should_start_scheduler() else None


if __name__ == "__main__":
    host = os.getenv("FLASK_RUN_HOST", "0.0.0.0")
    port = int(os.getenv("FLASK_RUN_PORT", "5000"))
    debug = os.getenv("FLASK_DEBUG", "true").lower() in {"1", "true", "yes"}
    app.run(host=host, port=port, debug=debug)
