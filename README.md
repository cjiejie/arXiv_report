# arXiv_report

抓取 arXiv 上与具身导航、立体视觉 SLAM、机械臂控制与规划相关的新论文，用大模型写成中文速览。配置了 Server酱 SendKey 时推送到微信，否则写成本地 Markdown。

## 它怎么筛论文

1. 只拉取近 24 小时内首次提交的论文，分类为 `cs.RO`、`cs.CV`、`cs.AI`、`cs.LG`、`cs.SY`、`eess.IV`。这些分类合在一次请求里，最多 500 篇。
2. 标题或摘要命中关键词才留下。更早的旧文更新不计入。
3. 在命中结果里取最新 30 篇交给模型总结。没有相关度打分，同一天里更早的命中论文会被截掉。

关键词覆盖这些方向：

| 方向 | 典型命中 |
|---|---|
| 机械臂运动控制 | inverse kinematics、visual servoing、compliant control、trajectory tracking |
| 机械臂规划 | OMPL、KDL、Ruckig、FCL、MoveIt、whole-body |
| 立体视觉与视觉 SLAM | stereo、depth estimation、visual odometry、visual SLAM、VINS、ORB-SLAM、RTAB-Map |
| 导航 | Nav2、A*、Hybrid A*、TEB、pure pursuit、MPPI、Fast-Planner、costmap、VLN |
| 建图 | state estimation、loop closure、OctoMap、elevation mapping、Voxblox、Nvblox、Cartographer、LOAM、FAST-LIO |

模型会把每篇归到其中一个标签：具身人形、机械臂、立体视觉与视觉 SLAM、导航、建图与状态估计。

## 环境

- Python 3.11
- 可访问 arXiv API 和 DeepSeek API

```bash
pip install requests openai
```

## 本地运行

`LLM_API_KEY` 必填。不设置 `SERVERCHAN_SENDKEY` 时，结果写到当前目录的 `YYYY.MM.DD_SLAM_Navigation_daily_精选.md`。

```bash
export LLM_API_KEY=你的DeepSeek密钥
export SERVERCHAN_SENDKEY=你的Server酱SendKey   # 可选
python daily_paper_crawler.py
```

没有命中论文时程序直接结束，不推送、不写文件。单篇总结失败时，该篇保留英文标题，贡献记为「提炼失败，请点击链接查看原文。」

## 定时推送

`.github/workflows/daily-paper.yml` 在周一到周五 UTC 00:30（北京时间 08:30）运行，也可以在 GitHub 的 Actions 页面手动触发。

在仓库 Secrets 里配置：

| Secret | 作用 |
|---|---|
| `LLM_API_KEY` | DeepSeek API 密钥，必填 |
| `SERVERCHAN_SENDKEY` | [Server酱 Turbo](https://sct.ftqq.com/) SendKey。不配置时，Markdown 只写在 Actions 运行器上，任务结束后就没了 |

## 改筛选范围

常量都在 `daily_paper_crawler.py` 顶部：

| 常量 | 当前值 | 作用 |
|---|---|---|
| `ARXIV_CATEGORIES` | 见上文 | arXiv 分类 |
| `MAX_RESULTS_PER_CAT` | `500` | 合并查询的篇数上限 |
| `RECENT_HOURS` | `24` | 只保留首次提交落在这个小时数内的论文 |
| `KEYWORDS` | 见脚本内分组 | 标题和摘要的正则 |
| `LLM_BASE_URL` | `https://api.deepseek.com/v1` | OpenAI 兼容接口 |
| `MODEL_NAME` | `deepseek-chat` | 总结所用模型 |

精选篇数在 `main` 里写死为 `papers[:30]`。

## 常见问题

**`请设置 LLM_API_KEY 环境变量`**  
当前环境里没有这个变量。GitHub Actions 里检查仓库 Secrets 是否叫 `LLM_API_KEY`。

**`arXiv 请求失败`**  
导出接口短暂不可用，或本机访问 `export.arxiv.org` 受限。过几分钟再跑。

**筛出来的论文很少，或几乎都是视觉论文**  
`cs.CV`、`cs.LG` 更新更密，会占满这 500 篇窗口。可以收窄 `ARXIV_CATEGORIES`，或加大 `MAX_RESULTS_PER_CAT`。

**微信没收到**  
确认 SendKey 属于 Turbo 版，且脚本打印的是推送成功。失败时会打印 Server酱返回的 JSON。
