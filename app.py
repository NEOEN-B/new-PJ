import os
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Dict, List, Optional

import feedparser
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

# 简单内存缓存，避免每次刷新都重复调用模型
CACHE: Dict[str, List[Dict[str, str]]] = {}


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
        return parsedate_to_datetime(candidate)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)


def fetch_latest_ai_articles(limit: int = 30) -> List[Dict[str, str]]:
    """抓取最新 AI 资讯并按时间倒序排序。"""
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

    # 去重（同 URL）
    dedup: Dict[str, Dict[str, str]] = {}
    for item in articles:
        dedup[item["url"]] = item

    sorted_items = sorted(
        dedup.values(), key=lambda x: x["published"], reverse=True
    )
    return sorted_items[:limit]


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


def score_article(article: Dict[str, str]) -> int:
    """按关键词给资讯打分，用于挑选更重要内容。"""
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

    # 新近内容加分
    age_hours = (datetime.now(timezone.utc) - article["published"]).total_seconds() / 3600
    if age_hours <= 24:
        score += 3
    elif age_hours <= 72:
        score += 1

    return score


def build_daily_digest() -> List[Dict[str, str]]:
    """构建当天资讯摘要列表（5~10 条）。"""
    day_key = datetime.now(CN_TZ).strftime("%Y-%m-%d")
    if day_key in CACHE:
        return CACHE[day_key]

    api_key = os.getenv("OPENAI_API_KEY")
    client = OpenAI(api_key=api_key) if api_key else None

    candidates = fetch_latest_ai_articles(limit=40)
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

    CACHE[day_key] = result
    return result


@app.route("/")
def index():
    items = build_daily_digest()
    return render_template(
        "index.html",
        items=items,
        updated_at=datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M"),
    )


@app.route('/refresh', methods=['POST'])
def refresh_news():
    """手动刷新当天资讯缓存并回到首页。"""
    day_key = datetime.now(CN_TZ).strftime("%Y-%m-%d")
    CACHE.pop(day_key, None)

    # 保留一个可扩展参数，便于未来接入按源刷新
    _ = request.form.get("source", "all")
    return redirect(url_for("index"))


if __name__ == "__main__":
    host = os.getenv("FLASK_RUN_HOST", "0.0.0.0")
    port = int(os.getenv("FLASK_RUN_PORT", "5000"))
    debug = os.getenv("FLASK_DEBUG", "true").lower() in {"1", "true", "yes"}
    app.run(host=host, port=port, debug=debug)
