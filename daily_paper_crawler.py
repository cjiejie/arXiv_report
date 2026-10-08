import os
import re
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
import requests
from openai import OpenAI

# ================= 配置区域 =================
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
SERVERCHAN_SENDKEY = os.getenv("SERVERCHAN_SENDKEY", "")  # 填入或从环境变量读取

LLM_BASE_URL = "https://api.deepseek.com/v1"
MODEL_NAME = "deepseek-chat"

ARXIV_CATEGORIES = ["cs.RO", "cs.CV","cs.AI", "eess.IV", "cs.LG","cs.SY"]
MAX_RESULTS_PER_CAT = 500
RECENT_HOURS = 24

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
    r"\bwhole[- ]body\b",
    # 立体视觉、深度与常见视觉 SLAM
    r"\bstereo\b",
    r"\bdepth estimation\b",
    r"\bvisual odometry\b",
    r"\bvisual slam\b",
    r"\bvisual[- ]inertial\b",
    r"\bbundle adjustment\b",
    r"\borb[- ]?slam\d*\b",
    r"\borb3\b",
    r"\brtab[- ]?map\b",
    r"\bvins(?:[- ](?:mono|fusion))?\b",
    r"\bopenvins\b",
    # 导航
    r"\bnavigation2\b",
    r"\ba(?:\*(?!\w)|[- ]?star\b)",
    r"\bhybrid a(?:\*(?!\w)|[- ]?star\b)",
    r"\bteb\b",
    r"\btimed elastic band\b",
    r"\bpure pursuit\b",
    r"\bmppi\b",
    r"\bfast[- ]?planner\b",
    r"\bmodel predictive path integral\b",
    r"\bobstacle avoidance\b",
    r"\btraversabilit(?:y|ies)\b",
    r"\bcostmap\b",
    r"\bVLN\b",
    r"\bDRLN\b",
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

def arxiv_query_time(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%d%H%M")

def parse_arxiv_time(text: str) -> datetime:
    return datetime.strptime(text.strip(), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)

# ================= 1. arXiv 抓取与过滤 =================
def fetch_arxiv_papers():
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=RECENT_HOURS)
    categories = " OR ".join(f"cat:{cat}" for cat in ARXIV_CATEGORIES)
    search_query = (
        f"({categories}) AND "
        f"submittedDate:[{arxiv_query_time(cutoff)} TO {arxiv_query_time(now)}]"
    )
    params = {
        "search_query": search_query,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "max_results": MAX_RESULTS_PER_CAT,
    }

    print(f"[*] 正在抓取近 {RECENT_HOURS} 小时的 arXiv 论文...")
    resp = requests.get("http://export.arxiv.org/api/query", params=params, timeout=20)
    if resp.status_code != 200:
        raise RuntimeError(f"arXiv 请求失败: {resp.status_code}")

    root = ET.fromstring(resp.content)
    namespace = {"atom": "http://www.w3.org/2005/Atom"}

    filtered_papers = []
    regex_pattern = re.compile("|".join(KEYWORDS), re.IGNORECASE)

    for entry in root.findall("atom:entry", namespace):
        published_elem = entry.find("atom:published", namespace)
        if published_elem is None or not published_elem.text:
            continue
        if parse_arxiv_time(published_elem.text) < cutoff:
            continue

        title = entry.find("atom:title", namespace).text.strip().replace("\n", " ")
        summary = entry.find("atom:summary", namespace).text.strip().replace("\n", " ")

        if regex_pattern.search(f"{title} {summary}"):
            paper_id_full = entry.find("atom:id", namespace).text.strip()
            arxiv_id = paper_id_full.split("/abs/")[-1]

            authors = [a.find("atom:name", namespace).text for a in entry.findall("atom:author", namespace)]
            authors_str = ", ".join(authors[:3]) + (" 等" if len(authors) > 3 else "")

            category_elem = entry.find("atom:category", namespace)
            primary_cat = category_elem.attrib.get("term", "cs.RO") if category_elem is not None else "cs.RO"

            filtered_papers.append({
                "arxiv_id": arxiv_id,
                "title_en": title,
                "summary": summary,
                "authors": authors_str,
                "category": primary_cat,
                "abs_url": f"https://arxiv.org/abs/{arxiv_id}",
                "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}.pdf"
            })

    print(f"[+] 近 {RECENT_HOURS} 小时内筛选出 {len(filtered_papers)} 篇相关论文。")
    return filtered_papers

# ================= 2. LLM 提炼总结 =================
def analyze_paper_with_llm(client: OpenAI, paper: dict) -> dict:
    prompt = f"""你是一名机器人导航、机械臂与 SLAM 领域的资深研究员。请阅读以下 arXiv 论文信息：

英文标题: {paper['title_en']}
摘要: {paper['summary']}

请严格按照以下 JSON 格式输出，不要包含 Markdown 语法标记或任何多余文字：
{{
  "title_cn": "准确严谨的中文直译标题，保留学术专有名词（如 NeRF、LiDAR、Factor Graph 等）",
  "contribution": "中文精炼概括：针对什么痛点(当前什么问题、痛点、难点) + 提出了什么方法 + 达到了什么效果（不超过500字）",
  "has_code": true或false,
  "code_url": "若摘要中提及了 GitHub/开源链接则提取，否则留空",
  "sub_field": "归类到以下标签之一：[具身人形, 机械臂, 立体视觉与视觉 SLAM, 导航, 建图与状态估计]"
}}"""

    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            response_format={"type": "json_object"}
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        print(f"[-] LLM 处理失败: {e}")
        return {
            "title_cn": paper['title_en'],
            "contribution": "提炼失败，请点击链接查看原文。",
            "has_code": False,
            "code_url": "",
            "sub_field": paper['category']
        }

# ================= 3. 生成可粘贴至微信公众号的纯文本 =================
def build_markdown_content(papers_data: list) -> str:
    """构建可直接复制到微信公众号编辑器的纯文本内容"""
    date_str = datetime.now().strftime("%Y年%m月%d日")
    lines = [
        f"【SLAM / 导航 / 机械臂速览】",
        f"{date_str}",
        "",
        f"今日精选 {len(papers_data)} 篇 arXiv 最新论文",
        "",
        "━━━━━━━━━━━━━━",
        "",
    ]

    for idx, item in enumerate(papers_data, 1):
        lines.append(f"0{idx}｜{item['title_cn']}" if idx < 10 else f"{idx}｜{item['title_cn']}")
        lines.append("")
        lines.append(f"　　原题：{item['title_en']}")
        lines.append(f"　　分类：{item.get('sub_field', item['category'])}")
        lines.append(f"　　作者：{item['authors']}")
        lines.append("")
        lines.append(f"　　{item['contribution']}")
        lines.append("")
        lines.append(f"　　▸ arXiv　{item['abs_url']}")
        lines.append(f"　　▸ PDF　　{item['pdf_url']}")
        if item.get("code_url"):
            lines.append(f"　　▸ 代码　　{item['code_url']}")
        lines.append("")
        lines.append("━━━━━━━━━━━━━━")
        lines.append("")

    return "\n".join(lines)

def send_to_serverchan(title: str, desp: str, sendkey: str):
    """通过 Server酱 Turbo 版推送至微信"""
    url = f"https://sctapi.ftqq.com/{sendkey}.send"
    payload = {
        "title": title,
        "desp": desp
    }
    resp = requests.post(url, data=payload, timeout=15)
    result = resp.json()
    if result.get("code") == 0:
        print("[+] 成功推送至 Server酱！微信即将收到卡片通知。")
    else:
        print(f"[-] Server酱推送失败: {result}")

# ================= 主流程 =================
def main():
    if not LLM_API_KEY:
        raise ValueError("请设置 LLM_API_KEY 环境变量")

    client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)

    # 1. 抓取与正则初筛
    papers = fetch_arxiv_papers()
    if not papers:
        print("[*] 今日无相关论文，跳过推送。")
        return

    # 精选前 30 篇
    selected_papers = papers[:30]
    processed_papers = []

    # 2. LLM 提炼
    for idx, paper in enumerate(selected_papers, 1):
        print(f"    -> [{idx}/{len(selected_papers)}] 正在提炼: {paper['arxiv_id']}")
        meta = analyze_paper_with_llm(client, paper)
        paper.update(meta)
        processed_papers.append(paper)

    # 3. 组装 Markdown 并推送
    date_str = datetime.now().strftime("%Y.%m.%d")
    md_content = build_markdown_content(processed_papers)
    if SERVERCHAN_SENDKEY:
        send_to_serverchan(
            title=f"{date_str} SLAM/Navigation 每日精选",
            desp=md_content,
            sendkey=SERVERCHAN_SENDKEY
        )
    else:
        out_path = f"{date_str}_SLAM_Navigation_daily_精选.md"
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(md_content)
        print(f"[+] 未设置 SERVERCHAN_SENDKEY，已写入 {out_path}")

if __name__ == "__main__":
    main()
