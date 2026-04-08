# LingXing WMS Pending Count

抓取领星 WMS `待处理` 订单总数，并支持：

- 手动抓取
- OpenClaw 定时任务执行
- 发送结果到企业微信

## 目录

- `run_pending_count.py`：抓取并发送企微
- `run_chat_fetch.py`：在当前会话里手动触发抓取
- `install_cron_job.py`：向 OpenClaw cron store 写入每日 17:00 定时任务
- `scripts/lingxing_wms_scraper.py`：通用 Playwright 页面抓取器
- `references/parcel-pending-total.job.json`：当前领星 WMS 页面抓取配置

## 环境要求

- Python 3.9+
- Node.js / npm
- Playwright for Python
- OpenClaw CLI
- 已配置企业微信频道（如需发送企微）

建议安装：

```bash
python3 -m pip install playwright
python3 -m playwright install chromium
```

## 首次使用

### 1. 保存自己的领星登录态

```bash
python3 scripts/lingxing_wms_scraper.py save-state \
  --login-url https://wms.xlwms.com/login \
  --state-file tmp/lingxing-state.json
```

登录完成后保存状态文件。不要共享这个文件。

### 2. 手动验证抓取

```bash
python3 run_pending_count.py
```

### 3. 安装 OpenClaw 定时任务

```bash
python3 install_cron_job.py \
  --cron-store ~/.openclaw/cron/jobs.json \
  --workspace ~/.openclaw/workspace
```

## 企微发送目标

默认读取环境变量：

- `LINGXING_WMS_WECOM_TARGET`

例如：

```bash
export LINGXING_WMS_WECOM_TARGET=JingYu
python3 run_pending_count.py
```

## 不要上传的文件

- `tmp/lingxing-state.json`
- `output/history.jsonl`
- `output/latest.json`
- 任何个人 Cookie、登录态、接收人配置
