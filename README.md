# 每日 AI 资讯摘要（Flask）

一个可本地直接运行的 AI 资讯网页工具：

1. 每天抓取网络最新 AI 资讯（技术、博客、心得等）
2. 使用 GPT 自动生成中文摘要（每条不超过 300 字）
3. 综合重要性挑选 5-10 条展示
4. 支持网页手动刷新 + 每天上午 8:00 自动刷新
5. 当天摘要持久化保存到 `data/summaries.json`（重启不丢失）

## 一、快速启动

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# 编辑 .env，填入 OPENAI_API_KEY（可选）
python app.py
```

浏览器打开：`http://127.0.0.1:5000`

## 二、环境变量说明（.env）

项目启动时会自动读取 `.env`（通过 `python-dotenv`）。

- `OPENAI_API_KEY`：OpenAI API Key。
  - 获取地址：<https://platform.openai.com/api-keys>
  - 不配置也可运行：会回退为 RSS 简介截断摘要。
- `OPENAI_MODEL`：可选，默认 `gpt-4o-mini`。
- `FLASK_RUN_HOST`：可选，默认 `0.0.0.0`。
- `FLASK_RUN_PORT`：可选，默认 `5000`。
- `FLASK_DEBUG`：可选，默认 `true`。

可直接参考 `.env.example`。

## 三、核心能力说明

- **自动任务**：使用 `APScheduler` 每天北京时间 **08:00** 自动抓取并生成当天摘要。
- **持久化**：摘要写入 `data/summaries.json`，服务重启后仍可读取。
- **去重策略**：
  - 先按 URL 去重；
  - 再按标题相似度去重（避免多站转载重复展示）。
- **排序策略**：综合关键词、时效性、来源权重评分。
- **来源权重**（更高代表更优先）：
  - OpenAI / Anthropic / Google AI Blog（高）
  - MIT Technology Review（较高）
- **手动刷新**：页面“手动刷新资讯”按钮可立即重算并覆盖当天结果。

## 四、目录结构

```text
.
├── app.py
├── requirements.txt
├── .env.example
├── data/
│   └── summaries.json
├── templates/
│   └── index.html
└── static/
    └── style.css
```
