# 每日 AI 资讯摘要（Flask）

一个简单的网页工具：

1. 每天抓取网络上的最新 AI 资讯（技术、博客、心得等）
2. 使用 GPT 自动生成中文摘要（每条不超过 300 字）
3. 自动挑选最重要的 5-10 条资讯展示
4. 点击即可查看原文

## 1) 本地运行（可直接启动）

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# 编辑 .env，填入 OPENAI_API_KEY（可选）
python app.py
```

打开：`http://127.0.0.1:5000`

## 2) `.env` 配置说明

项目启动时会自动读取 `.env`（基于 `python-dotenv`）。

- `OPENAI_API_KEY`：OpenAI API Key。
  - 获取路径：`https://platform.openai.com/api-keys`
  - 不配置时：系统仍可运行，但摘要将回退为 RSS 原文简介截断版本。
- `OPENAI_MODEL`：可选，默认 `gpt-4o-mini`。
- `FLASK_RUN_HOST`：可选，默认 `0.0.0.0`。
- `FLASK_RUN_PORT`：可选，默认 `5000`。
- `FLASK_DEBUG`：可选，默认 `true`。

示例请参考：`.env.example`。

## 3) 说明

- 资讯来源默认使用多家 AI 相关 RSS（可在 `app.py` 里修改 `RSS_SOURCES`）。
- 每日结果使用内存缓存（`CACHE`），当天刷新不会重复请求模型。
- 页面提供“手动刷新资讯”按钮，可清空当天缓存并重新生成。
- 若模型调用失败，会自动回退到原文简介，保证页面可用。
