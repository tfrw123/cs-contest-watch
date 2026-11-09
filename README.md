# CS Contest Watch v8

六项计算机竞赛官方页面监测与 Timeline：USACO、ACSL、CCC、BCC、AIO、NZIC/NZOI。

## 部署方式（由你自己的 GitHub 账户执行）

1. 下载并解压 `cs-contest-watch-v8.zip`。
2. 在你希望使用的 GitHub 账户下新建一个空仓库，例如 `cs-contest-watch`。
3. 将本目录内的全部文件上传到仓库根目录，默认分支建议使用 `main`。
4. 打开仓库 **Settings → Pages**，将 **Build and deployment → Source** 设为 **GitHub Actions**。
5. 打开 **Actions** 标签页，启用工作流；手动运行一次 `CS Contest Watch - Monitor and Deploy`。
6. 工作流成功后，在仓库 **Settings → Pages** 查看站点地址。

无需配置 GitHub Secrets；工作流使用该仓库自动提供的 `GITHUB_TOKEN`。本项目不访问或依赖其他 GitHub 账户。

## 监测策略

- USACO：GitHub Actions 每 30 分钟执行一次检查。
- ACSL、CCC、BCC、AIO、NZIC：每次工作流执行时检查，但同一赛事两次检查至少间隔 6 小时。
- 比较官方页面正文哈希；检测到变化时写入 `data/history.json`。
- USACO 解析器只在页面出现明确的 `2026–2027 ... Schedule` 标题时尝试提取日期，不以往年赛程推测。
- 成功解析后将结构化赛程写入 `data/contests.json`，网页 Timeline 读取该文件。
- 网页只展示最近一次工作流成功写入的数据；不是浏览器端实时抓取。

## 重要限制

1. **GitHub Actions 的定时任务并非硬实时 SLA**。GitHub 可能延迟排队；“每 30 分钟”是计划频率，不保证官网发布后 30 分钟内必然显示。
2. 当前 USACO 解析器是保守的 HTML 文本解析器。若官网改版、赛程标题或日期格式变化，解析可能失败；此时会保留 TBD，而不会猜测。
3. ACSL、CCC、BCC、AIO、NZIC 当前先做官方页面变更监测。页面有变化会记录来源与检测时间，但**不代表已经具备可靠的自动日期抽取**；需要逐站点适配器并经人工核验后才能自动同步赛程。
4. 网站内容变更不一定是报名或赛程变化。历史记录会明确标注为“官方页面内容发生变化，请核对”，避免误报成“报名已开放”。
5. `data/contests.json` 中已有的静态赛事介绍与日期是此前版本的初始数据。上线前请逐项打开官方来源核实；未经核实的信息不应当视为官方确认。
6. GitHub Pages 为静态托管；监测由 Actions 后端执行，浏览器刷新只读取最新生成的数据文件。

## 文件结构

```text
.
├── index.html
├── data/
│   ├── contests.json
│   ├── history.json
│   └── monitor-status.json
├── scripts/
│   └── monitor.py
└── .github/workflows/
    └── monitor-and-deploy.yml
```

## 本地预览

```bash
python -m http.server 8000
```

访问 `http://localhost:8000`。

## 调整监测频率

- USACO cron 在 `.github/workflows/monitor-and-deploy.yml` 中修改。
- 其他赛事的最小检查间隔在 `scripts/monitor.py` 中的 `timedelta(hours=6)` 修改。
