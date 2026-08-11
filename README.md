# XAUUSD Forecaster

一个研究 **XAUUSD（黄金）未来 30 分钟方向**的预估系统。

系统每 5 分钟产生一次 `LONG`、`SHORT` 或 `WAIT`，30 分钟后记录真实
Bid/Ask 结果，再用已经成熟的结果训练下一组模型。

**它不会自动下单，也不会连接账户执行交易。**

[查看实时研究面板](https://aurum-signal-room.yiyousiow1234.workers.dev/)

## 可以看到什么

- 当前 30 分钟方向预估；
- 每次预估对应的真实 30 分钟结果；
- 只看黄金与加入新闻后的模型成绩；
- 模型在决策前实际看过的新闻证据；
- 数据来源、组件和同步状态。

## 如何工作

```text
cTrader Bid/Ask + 有时间记录的新闻
                 ↓
       Collector / Annotator
                 ↓
       固定版本的 Shadow 模型
                 ↓
     30 分钟后记录结果并继续学习
```

所有预测都会先冻结再等待结果。迟到的新闻不能修改过去的预测，旧模型也不会
因为新数据被重新改写。

## 研究边界

- 只研究 XAUUSD；
- 固定每 5 分钟预测、观察未来 30 分钟；
- 使用真实 Bid/Ask 与可追溯的新闻时间；
- 所有模型目前都是 Shadow 研究，不具备下单权限；
- 模型版本只能由项目负责人手动批准切换。

## 本机运行

Windows 用户通过统一控制中心启动 Collector、Annotator、Dashboard API 和同步：

```powershell
powershell -File scripts/xauusd_control_center.ps1
```

运行测试：

```powershell
python -m pytest -q tests
```

数据库、日志、行情、模型文件和其他运行产物保存在忽略提交的
`.local/forward/`，不会上传到 GitHub。

## 详细文档

- [产品规则](docs/PRODUCT_CONTRACT.md)
- [系统与数据边界](docs/SYSTEM_CONTRACT.md)
- [Forward-only 学习规则](docs/FORWARD_ONLY_CONTRACT.md)
- [Cloudflare 部署说明](docs/CLOUDFLARE_HOSTING.md)
