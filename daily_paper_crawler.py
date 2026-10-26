import os
import re
import json
import xml.etree.ElementTree as ET
from datetime import datetime
import requests

# ================= 配置区域 =================
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
SERVERCHAN_SENDKEY = os.getenv("SERVERCHAN_SENDKEY", "")

LLM_BASE_URL = "https://api.deepseek.com/v1"
MODEL_NAME = "deepseek-chat"
LLM_TIMEOUT = 30

# True：不推送 Server酱，把 Markdown 写到本地 output/日期.md
LOCAL_MARKDOWN = False

# 每日最多提炼并输出的论文篇数
MAX_PAPERS = 30

ARXIV_CATEGORIES = ["cs.RO", "cs.CV", "cs.AI", "eess.IV", "cs.LG", "cs.SY"]

KEYWORDS = [
    # 机械臂运动控制
    r"\binverse kinematics\b",
    r"\bvisual servoing\b",
    r"\bcompliant control\b",
    r"\btrajectory tracking\b",
    # 机械臂规划
    r"\bompl\b",
    r"\bkdl\b",
    r"\bruckig\b",
    r"\bfcl\b",
    r"\bmoveit\b",
    # 立体视觉、深度与常见视觉 SLAM
    r"\bstereo\b",
    r"\bdepth estimation\b",
    r"\bvisual odometry\b",
    r"\bslam\b",
    r"\bvisual[- ]inertial\b",
    r"\bbundle adjustment\b",
    r"\borb3\b",
    r"\brtab[- ]?map\b",
    r"\bvins(?:[- ](?:mono|fusion))?\b",
    r"\bopenvins\b",
    r"\bsfm\b",
    r"\b3dgs\b",
    r"\biros\b",
    r"\beccv\b",
    r"\bcvrp\b",
    # 导航
    r"\bnavigation2\b",
    r"\ba(?:\*(?!\w)|[- ]?star\b)",
    r"\bhybrid a(?:\*(?!\w)|[- ]?star\b)",
    r"\bteb\b",
    r"\btimed elastic band\b",
    r"\bpure pursuit\b",
    r"\bmppi\b",
    r"\btsp\b",
    r"\bdwa\b",
    r"\bdwb\b",
    r"\bfast[- ]?planner\b",
    r"\bmodel predictive path integral\b",
    r"\btraversabilit(?:y|ies)\b",
    r"\bcostmap\b",
    r"\bvln\b",
    r"\bdrln\b",
    r"\bnavid\b",
    r"\bsmartway\b",
    r"\bturtlebot\b",
    # 建图
    r"\bstate estimation\b",
    r"\bloop closure\b",
    r"\boctomap\b",
    r"\belevation map(?:ping)?\b",
    r"\bvoxblox\b",
    r"\bnvblox\b",
    r"\bfiesta\b",
    r"\b(?:t|e)sdf\b",
    r"\bcartographer\b",
    r"\b(?:lego[- ])?loam\b",
    r"\blio[- ]sam\b",
    r"\bfast(?:er)?[- ]lio\d*\b",
    r"\bkiss[- ]icp\b",
    r"\bkinectfusion\b",
    r"\belasticfusion\b"
]

# ================= 1. arXiv 抓取与过滤（基于官方每日 RSS） =================
def fetch_arxiv_papers():
    cats_query = "+".join(ARXIV_CATEGORIES)
    rss_url = f"https://rss.arxiv.org/rss/{cats_query}"

    print(f"[*] 正在通过 arXiv RSS 抓取今日最新公布论文: {rss_url}")
    resp = requests.get(rss_url, timeout=60)
    if resp.status_code != 200:
        raise RuntimeError(f"arXiv RSS 请求失败: {resp.status_code}")

    root = ET.fromstring(resp.content)
    channel = root.find("channel")
    if channel is None:
        print("[*] RSS 响应中未找到 channel 节点。")
        return []

    filtered_papers = []
    seen_ids = set()
    regex_pattern = re.compile("|".join(KEYWORDS), re.IGNORECASE)

    ns = {
        "dc": "http://purl.org/dc/elements/1.1/",
        "arxiv": "http://arxiv.org/schemas/atom"
    }

    all_items = channel.findall("item")
    print(f"[*] RSS 今日共返回 {len(all_items)} 条原始记录，开始过滤...")

    for item in all_items:
        announce_type = item.find("arxiv:announce_type", ns)
        if announce_type is not None and announce_type.text:
            if announce_type.text.strip() not in ("new", "cross"):
                continue

        link = item.findtext("link", "").strip()
        arxiv_id = link.split("/abs/")[-1].split("v")[0]
        if not arxiv_id or arxiv_id in seen_ids:
            continue
        seen_ids.add(arxiv_id)

        title = item.findtext("title", "").strip().replace("\n", " ")
        title = re.sub(r"\s*\(arXiv:[^\)]+\)$", "", title)

        desc = item.findtext("description", "").strip().replace("\n", " ")
        summary = desc.split("Abstract:", 1)[-1].strip() if "Abstract:" in desc else desc
        summary = re.sub(r"<[^>]+>", " ", summary)
        summary = " ".join(summary.split())

        if regex_pattern.search(f"{title} {summary}"):
            creator_text = item.findtext("dc:creator", "", ns)
            authors = [re.sub(r"<[^>]+>", "", a).strip() for a in creator_text.split(",") if a.strip()]
            authors_str = ", ".join(authors[:3]) + (" 等" if len(authors) > 3 else "")
            if not authors_str:
                authors_str = "作者未知"

            categories = [c.text.strip() for c in item.findall("category") if c.text]
            primary_cat = categories[0] if categories else "cs.RO"

            filtered_papers.append({
                "arxiv_id": arxiv_id,
                "title_en": title,
                "summary": summary,
                "authors": authors_str,
                "category": primary_cat,
                "abs_url": f"https://arxiv.org/abs/{arxiv_id}",
                "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}.pdf"
            })

    filtered_papers.sort(key=lambda x: (0 if x["category"] == "cs.RO" else 1))

    print(f"[+] 今日共筛选出 {len(filtered_papers)} 篇相关论文。")
    return filtered_papers

# ================= 2. LLM 提炼总结（纯 requests 实现，免疫代理 Bug） =================
def analyze_paper_with_llm(paper: dict) -> dict:
    prompt = (
        "你是一名机器人导航、机械臂与 SLAM 领域的资深研究员。请阅读以下 arXiv 论文信息：\n\n"
        f"英文标题: {paper['title_en']}\n"
        f"摘要: {paper['summary']}\n\n"
        "请严格按照以下 JSON 格式输出，不要包含 Markdown 语法标记或任何多余文字：\n"
        "{\n"
        '  "title_cn": "准确严谨的中文直译标题，保留学术专有名词（如 NeRF、LiDAR、Factor Graph 等）",\n'
        '  "contribution": "中文精炼概括：针对什么痛点(当前什么问题、痛点、难点) + 提出了什么方法 + 达到了什么效果（不超过300字）",\n'
        '  "has_code": true或false,\n'
        '  "code_url": "若摘要中提及了 GitHub/开源链接则提取，否则留空",\n'
        '  "sub_field": "归类到以下标签之一：[具身人形, 机械臂, 立体视觉与视觉 SLAM, 导航, 建图与状态估计]"\n'
        "}"
    )

    url = f"{LLM_BASE_URL.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {LLM_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": MODEL_NAME,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "response_format": {"type": "json_object"}
    }

    try:
        # DeepSeek 为国内直连 API，trust_env=False 可直接绕过本地代理环境变量干扰
        session = requests.Session()
        session.trust_env = False
        resp = session.post(url, headers=headers, json=payload, timeout=LLM_TIMEOUT)
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        return json.loads(content)
    except Exception as e:
        print(f"[-] LLM 处理失败: {e}")
        return {
            "title_cn": paper['title_en'],
            "contribution": "提炼失败，请点击链接查看原文。",
            "has_code": False,
            "code_url": "",
            "sub_field": paper['category']
        }

# ================= 3. 组装正文并推送 Server酱 =================
FIELD_ORDER = (
    "具身人形",
    "机械臂",
    "立体视觉与视觉 SLAM",
    "导航",
    "建图与状态估计",
)

def md_text(text: str) -> str:
    return re.sub(r"([\\`*_\[\]])", r"\\\1", text or "")

def paper_field(item: dict) -> str:
    return (item.get("sub_field") or item.get("category") or "其他").strip() or "其他"

def one_line(text: str) -> str:
    return " ".join((text or "").split())

def normalize_url(url: str) -> str:
    url = re.sub(r"[.,;，。]+$", "", (url or "").strip())
    if url.startswith("www.") or url.startswith("github.com/"):
        return "https://" + url
    return url

def plain_url(url: str) -> str:
    url = normalize_url(url)
    if not url.startswith(("http://", "https://")):
        return ""
    return url.replace(" ", "%20").replace("|", "%7C")

def group_papers(papers_data: list) -> list:
    buckets = {}
    for idx, item in enumerate(papers_data, 1):
        buckets.setdefault(paper_field(item), []).append((idx, item))
    ordered = []
    for name in FIELD_ORDER:
        if name in buckets:
            ordered.append((name, buckets.pop(name)))
    ordered.extend(buckets.items())
    return ordered

def paper_title(item: dict) -> str:
    return md_text(one_line(item.get("title_cn") or item.get("title_en") or "无标题"))


def render_paper(idx: int, item: dict) -> list:
    title = paper_title(item)
    title_en = md_text(one_line(item.get("title_en") or "—"))
    arxiv_id = item.get("arxiv_id") or "编号未知"
    field = md_text(paper_field(item))
    authors = md_text(item.get("authors") or "作者未知")
    contribution = md_text(one_line(item.get("contribution") or "暂无概括"))
    link_lines = []
    for label, key in (("摘要", "abs_url"), ("PDF", "pdf_url"), ("代码", "code_url")):
        url = plain_url(item.get(key))
        if url:
            link_lines.append(f"- {label}：`{url}`")
    if not link_lines:
        link_lines.append("- 暂无可用链接")
    return [
        f"### {idx:02d}. {title}",
        "",
        f"*{title_en}*",
        "",
        f"`{arxiv_id}` · {field} · {authors}",
        "",
        f"> {contribution}",
        "",
        *link_lines,
        "",
        "---",
        "",
    ]


def render_toc(groups: list) -> list:
    lines = ["## 目录", ""]
    for name, items in groups:
        lines.append(f"**{md_text(name)}** · {len(items)} 篇")
        lines.append("")
        for idx, item in items:
            lines.append(f"- {idx:02d}. {paper_title(item)}")
        lines.append("")
    return lines


def render_summary_table(groups: list) -> list:
    lines = [
        "| 分类 | 篇数 |",
        "| --- | ---: |",
    ]
    for name, items in groups:
        lines.append(f"| {md_text(name)} | {len(items)} |")
    lines.append("")
    return lines


def build_markdown_content(papers_data: list) -> str:
    date_str = datetime.now().strftime("%Y.%m.%d")
    groups = group_papers(papers_data)
    total = len(papers_data)

    lines = [
        "# 具身 / SLAM / Navigation 每日论文",
        "",
        f"{date_str} · 共 **{total}** 篇",
        "",
        *render_summary_table(groups),
        *render_toc(groups),
        "---",
        "",
    ]
    for name, items in groups:
        lines.extend([f"## {md_text(name)}", ""])
        for idx, item in items:
            lines.extend(render_paper(idx, item))
    return "\n".join(lines).strip() + "\n"

def serverchan_endpoint(sendkey: str) -> str:
    match = re.match(r"sctp(\d+)t", sendkey)
    if match:
        return f"https://{match.group(1)}.push.ft07.com/send/{sendkey}.send"
    return f"https://sctapi.ftqq.com/{sendkey}.send"

def save_markdown(content: str, date_str: str) -> str:
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{date_str}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"[+] 已写入本地 Markdown：{path}")
    return path


def send_to_serverchan(title: str, desp: str, sendkey: str):
    url = serverchan_endpoint(sendkey)
    payload = {
        "title": title,
        "desp": desp
    }
    session = requests.Session()
    session.trust_env = False
    resp = session.post(url, data=payload, timeout=15)
    result = resp.json()
    if result.get("code") == 0:
        print("[+] 成功推送至 Server酱！微信即将收到卡片通知。")
    else:
        print(f"[-] Server酱推送失败: {result}")

# ================= 主流程 =================
def main():
    if not LLM_API_KEY:
        raise ValueError("请设置 LLM_API_KEY 环境变量")
    if not LOCAL_MARKDOWN and not SERVERCHAN_SENDKEY:
        raise ValueError("请设置 SERVERCHAN_SENDKEY 环境变量")

    # 1. 抓取与正则初筛
    papers = fetch_arxiv_papers()
    if not papers:
        print("[*] 今日无相关论文（或恰逢周末休刊），跳过推送。")
        return

    selected_papers = papers[:MAX_PAPERS]
    processed_papers = []

    # 2. LLM 提炼
    for idx, paper in enumerate(selected_papers, 1):
        print(f"    -> [{idx}/{len(selected_papers)}] 正在提炼: {paper['arxiv_id']}", flush=True)
        meta = analyze_paper_with_llm(paper)
        paper.update(meta)
        processed_papers.append(paper)
        print(f"    -> [{idx}/{len(selected_papers)}] 提炼完成", flush=True)

    # 3. 组装正文；本地预览或推送 Server酱
    date_str = datetime.now().strftime("%Y.%m.%d")
    content = build_markdown_content(processed_papers)
    if LOCAL_MARKDOWN:
        save_markdown(content, date_str)
        return
    print("[*] 正在推送 Server酱...", flush=True)
    send_to_serverchan(
        title=f"{date_str} 具身/SLAM/Navigation 每日论文",
        desp=content,
        sendkey=SERVERCHAN_SENDKEY
    )

if __name__ == "__main__":
    main()
