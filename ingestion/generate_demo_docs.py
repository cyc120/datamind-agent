# -*- coding: utf-8 -*-
"""
generate_demo_docs.py — 生成 ≥10 份中文演示知识库 PDF
类型覆盖: 原生文本(多级目录) / 表格密集 / 跨页表格 / 公式 / 规章制度 /
         扫描风格(旋转+噪声) / 图文混排 / FAQ / 数据字典
关键设计: 表格数值取自 demo_retail 实际聚合，保证跨源一致性可验证。

用法: py -3.13 ingestion/generate_demo_docs.py
输出: data/knowledge_base/*.pdf + data/knowledge_base/manifest.json
"""
import io
import json
import random
import psycopg2
from datetime import datetime
from pathlib import Path

import pymupdf
from PIL import Image, ImageFilter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, Frame, Image as RLImage,
                                PageTemplate, Paragraph, Spacer, Table, TableStyle)

import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "knowledge_base"
OUT.mkdir(parents=True, exist_ok=True)

RNG = random.Random(77)

# ---------- 字体 ----------
pdfmetrics.registerFont(TTFont("hei", "C:/Windows/Fonts/simhei.ttf"))
pdfmetrics.registerFont(TTFont("song", "C:/Windows/Fonts/simfang.ttf"))

S_TITLE = ParagraphStyle("t", fontName="hei", fontSize=20, leading=30,
                         alignment=1, spaceAfter=14)
S_H1 = ParagraphStyle("h1", fontName="hei", fontSize=15, leading=22,
                      spaceBefore=14, spaceAfter=8)
S_H2 = ParagraphStyle("h2", fontName="hei", fontSize=12.5, leading=19,
                      spaceBefore=10, spaceAfter=6)
S_H3 = ParagraphStyle("h3", fontName="hei", fontSize=11, leading=17,
                      spaceBefore=8, spaceAfter=4)
S_BODY = ParagraphStyle("b", fontName="song", fontSize=10.5, leading=18,
                        firstLineIndent=21, spaceAfter=4)
S_CELL = ParagraphStyle("c", fontName="song", fontSize=9, leading=13, alignment=1)
S_NOTE = ParagraphStyle("n", fontName="song", fontSize=8.5, leading=13,
                        textColor=colors.grey)

PAGE_W, PAGE_H = A4


def header_footer(title):
    def draw(canv, doc):
        canv.saveState()
        canv.setFont("hei", 8)
        canv.setFillColor(colors.grey)
        canv.drawString(2 * cm, PAGE_H - 1.2 * cm, f"DataMind Agent 演示知识库 · {title}")
        canv.drawRightString(PAGE_W - 2 * cm, 1.2 * cm, f"第 {doc.page} 页")
        canv.line(2 * cm, PAGE_H - 1.4 * cm, PAGE_W - 2 * cm, PAGE_H - 1.4 * cm)
        canv.restoreState()
    return draw


def make_doc(story, title):
    doc = BaseDocTemplate(str(OUT / title), pagesize=A4,
                          leftMargin=2 * cm, rightMargin=2 * cm,
                          topMargin=2 * cm, bottomMargin=2 * cm)
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="f")
    doc.addPageTemplates([PageTemplate(id="p", frames=[frame],
                                       onPage=header_footer(title.replace(".pdf", "")))])
    doc.build(story)


def para(txt, style=S_BODY):
    return Paragraph(txt, style)


def data_table(header, rows, col_widths=None, caption=None):
    data = [[Paragraph(str(h), S_CELL) for h in header]] + \
           [[Paragraph(str(v), S_CELL) for v in r] for r in rows]
    t = Table(data, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2f4f7f")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f5fa")]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    out = [t]
    if caption:
        out.append(Spacer(1, 3))
        out.append(Paragraph(caption, S_NOTE))
        out.append(Spacer(1, 6))
    else:
        out.append(Spacer(1, 8))
    return out


def formula_png(latex, fname, fontsize=15):
    """渲染 LaTeX 公式为透明 PNG，返回可嵌入 story 的 RLImage"""
    fig = plt.figure(figsize=(0.1, 0.1))
    try:
        fig.text(0, 0, f"${latex}$", fontsize=fontsize)
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=200, bbox_inches="tight", transparent=True)
    finally:
        plt.close(fig)
    buf.seek(0)
    img = Image.open(buf)
    w, h = img.size
    img.save(OUT / fname)
    return RLImage(str(OUT / fname), width=w * 72 / 200 / 2, height=h * 72 / 200 / 2)


# ============================================================
# 从 demo_retail 取真实聚合，保证 文档↔数据库 一致性
# ============================================================
def fetch_db_aggregates():
    conn = psycopg2.connect(host="localhost", port=5432, dbname="demo_retail",
                            user="readonly_user", password="readonly_dev")
    cur = conn.cursor()
    # 2025 各大区季度销售额（万元）
    cur.execute("""
        SELECT r.region_name,
               EXTRACT(QUARTER FROM o.created_at)::int AS q,
               round(sum(oi.quantity * oi.unit_price * (1 - oi.discount)) / 10000.0, 1)
        FROM order_items oi
        JOIN orders o USING (order_id)
        JOIN customers c USING (customer_id)
        JOIN regions r USING (region_id)
        WHERE o.order_status IN ('completed','shipped','refunded')
          AND o.created_at >= '2025-01-01' AND o.created_at < '2026-01-01'
        GROUP BY 1, 2 ORDER BY 1, 2""")
    region_q = {}
    for region, q, amt in cur.fetchall():
        region_q.setdefault(region, {})[int(q)] = float(amt)
    # 2025 产品销售额 Top3
    cur.execute("""
        SELECT p.product_name, p.category,
               round(sum(oi.quantity * oi.unit_price * (1 - oi.discount)) / 10000.0, 1) AS amt,
               sum(oi.quantity) AS qty
        FROM order_items oi
        JOIN orders o USING (order_id)
        JOIN products p USING (product_id)
        WHERE o.order_status IN ('completed','shipped','refunded')
          AND o.created_at >= '2025-01-01' AND o.created_at < '2026-01-01'
        GROUP BY 1, 2 ORDER BY amt DESC LIMIT 3""")
    top3 = cur.fetchall()
    # 2024 全年销售额（万元）用于同比
    cur.execute("""
        SELECT round(sum(oi.quantity * oi.unit_price * (1 - oi.discount)) / 10000.0, 1)
        FROM order_items oi JOIN orders o USING (order_id)
        WHERE o.order_status IN ('completed','shipped','refunded')
          AND o.created_at >= '2024-01-01' AND o.created_at < '2025-01-01'""")
    y2024 = float(cur.fetchone()[0])
    cur.execute("""
        SELECT round(sum(oi.quantity * oi.unit_price * (1 - oi.discount)) / 10000.0, 1)
        FROM order_items oi JOIN orders o USING (order_id)
        WHERE o.order_status IN ('completed','shipped','refunded')
          AND o.created_at >= '2025-01-01' AND o.created_at < '2026-01-01'""")
    y2025 = float(cur.fetchone()[0])
    # 退款原因分布
    cur.execute("""SELECT reason, count(*) FROM refunds
                   WHERE refunded_at >= '2025-01-01' GROUP BY 1 ORDER BY 2 DESC""")
    refund_reasons = cur.fetchall()
    conn.close()
    return {"region_q": region_q, "top3": top3, "y2024": y2024,
            "y2025": y2025, "refund_reasons": refund_reasons}


# ============================================================
# 各文档构造器
# ============================================================

def doc_01_product_report(agg):
    """原生文本 + 三级目录 + 跨页表格。主打产品取自 DB 2025 销量冠军。"""
    champ = agg["top3"][0]
    name, cat, amt, qty = champ
    yoy = (agg["y2025"] / agg["y2024"] - 1) * 100 if agg["y2024"] else 0
    story = [
        para(f"{name}产品市场分析报告（2025 年度）", S_TITLE),
        para("——基于 DataMind 演示数据集 · 数据截至 2025-12-31 · 本报告数据为演示数据", S_NOTE),
        Spacer(1, 8),
        para("1 摘要", S_H1),
        para(f"2025 年度，{name}以约 {amt} 万元的销售额位居全品类第一，全年售出约 {int(qty)} 台。"
             f"公司整体销售额约 {agg['y2025']} 万元，同比增长 {yoy:.1f}%。增长主要来自华东与华南市场，"
             "以及 11-12 月促销季的集中放量。"),
        para("2 市场环境", S_H1),
        para("2.1 行业大盘", S_H2),
        para("2025 年家电行业整体温和复苏，线上渠道占比进一步提升至约六成。能效新规实施后，"
             "一级能效产品渗透率显著提高，变频产品成为主流选择。"),
        para("2.2 竞争格局", S_H2),
        para("2.2.1 价格带分布", S_H3),
        para("3000-3500 元价格带竞争最为激烈，本品牌主力机型卡位该区间；头部品牌以电机保修政策"
             "争夺换新用户。"),
        para("2.2.2 渠道结构", S_H3),
        para("线上商城贡献约半数成交，APP 端增长最快；线下门店承担体验与安装服务职能。"),
        para(f"3 {name}表现解读", S_H1),
        para("3.1 增长动因", S_H2),
        para(f"第一，产品力：{name}采用一级能效变频压缩机，静音表现突出，在主流电商平台的"
             "好评率长期保持领先。第二，价格策略：全年三次促销卡位竞品发布窗口，成交均价下探"
             "至敏感区间。第三，渠道协同：APP 端专享券带动复购与老带新。"),
        para("3.2 分季度销售数据", S_H2),
    ]
    rows = []
    regions = sorted(agg["region_q"].keys())
    for r in regions:
        qs = agg["region_q"][r]
        total = sum(qs.values())
        rows.append([r] + [f"{qs.get(q, 0):.1f}" for q in range(1, 5)] + [f"{total:.1f}"])
    rows.append(["全国合计"] + [f"{sum(agg['region_q'][r].get(q, 0) for r in regions):.1f}"
                              for q in range(1, 5)] +
                [f"{sum(sum(agg['region_q'][r].values()) for r in regions):.1f}"])
    story += data_table(
        ["大区", "Q1(万元)", "Q2(万元)", "Q3(万元)", "Q4(万元)", "全年(万元)"],
        rows, caption="表 3-1 2025 年各大区分季度销售额（数据来源：零售演示库 orders/order_items 聚合）")
    story += [
        para("3.3 风险提示", S_H2),
        para("原材料价格波动、能效标准进一步收紧、竞品价格战为 2026 年主要风险。"),
        para("4 结论与建议", S_H1),
        para("建议 2026 年延续“一级能效+静音”心智，加大 APP 端投放；针对华南市场补齐安装服务产能。"),
    ]
    make_doc(story, "01_重点产品市场分析报告_2025.pdf")
    return {"champion": name, "champion_sales_wan": amt}


def doc_02_channel_strategy():
    story = [
        para("渠道运营策略白皮书（V2.1）", S_TITLE),
        para("适用范围：线上商城 / APP / 线下门店 · 密级：内部公开", S_NOTE),
        Spacer(1, 8),
        para("1 渠道定位", S_H1),
        para("1.1 线上商城", S_H2),
        para("承接品牌搜索流量，主打高客单价家电与套购场景，是销售额基本盘。"),
        para("1.2 APP 渠道", S_H2),
        para("APP 承担会员运营与复购召回。策略要点："),
        para("· 每月 8 日会员日发放品类券；", S_BODY),
        para("· 新品首发优先 APP 端预约；", S_BODY),
        para("· 老带新双方各得积分，积分可抵现。", S_BODY),
        para("1.3 线下门店", S_H2),
        para("门店聚焦体验、安装与售后，承担大件家电的最后一公里履约。"),
        para("2 渠道考核口径", S_H1),
        para("渠道销售额按订单归属渠道统计，退款订单在退款发生当月冲减。口径详见《财务指标核算办法》。"),
        para("3 分渠道预算（演示数据）", S_H1),
    ]
    rows = [["线上商城", "180", "60%", "拉新+大促"],
            ["APP", "90", "30%", "会员复购"],
            ["线下门店", "30", "10%", "体验履约"]]
    story += data_table(["渠道", "年度预算(万元)", "占比", "核心目标"], rows,
                        caption="表 3-1 渠道预算分配（演示数据）")
    story += [
        para("4 禁止事项", S_H1),
        para("禁止跨渠道刷单套取积分；禁止门店截留线上订单佣金；所有优惠必须可追溯到活动编号。"),
    ]
    make_doc(story, "02_渠道运营策略白皮书.pdf")


def doc_03_region_table(agg):
    """表格密集型：多张小表。"""
    story = [
        para("2025 年分区域经营数据简报", S_TITLE),
        para("数据来源：零售演示库 · 单位：万元 · 本表为演示数据", S_NOTE),
        Spacer(1, 8),
        para("一、各季度大区销售额", S_H1),
    ]
    regions = sorted(agg["region_q"].keys())
    rows = [[r] + [f"{agg['region_q'][r].get(q, 0):.1f}" for q in range(1, 5)]
            for r in regions]
    story += data_table(["大区", "Q1", "Q2", "Q3", "Q4"], rows,
                        caption="表 1 各大区 2025 年分季度销售额（万元）")
    story.append(para("二、大区排名", S_H1))
    totals = sorted(((r, sum(agg["region_q"][r].values())) for r in regions),
                    key=lambda x: -x[1])
    rows = [[i + 1, r, f"{v:.1f}", f"{v / sum(t[1] for t in totals) * 100:.1f}%"]
            for i, (r, v) in enumerate(totals)]
    story += data_table(["名次", "大区", "全年销售额(万元)", "占比"], rows,
                        caption="表 2 大区全年排名")
    story.append(para("三、退款原因分布（2025）", S_H1))
    rows = [[i + 1, r, n] for i, (r, n) in enumerate(agg["refund_reasons"])]
    story += data_table(["序号", "退款原因", "笔数"], rows,
                        caption="表 3 退款原因统计")
    story.append(para("说明：以上数据与零售演示库实时聚合结果一致，可用于跨源一致性校验演示。", S_NOTE))
    make_doc(story, "03_2025年分区域经营数据简报.pdf")


def doc_04_refund_policy():
    story = [
        para("售后服务与退款管理制度", S_TITLE),
        para("制度编号：DM-KF-2024-011 · 生效日期：2024-06-01", S_NOTE),
        Spacer(1, 8),
        para("第一章 总则", S_H1),
        para("第一条 本制度适用于公司全渠道销售订单的退换货管理。"),
        para("第二条 退款须遵循“先退款、后揽件”或“揽件验收后退款”两种流程之一，禁止未验收直接全额退款。"),
        para("第二章 退款条件", S_H1),
        para("第三条 自签收之日起 7 日内，商品未拆封且不影响二次销售的，支持七天无理由退款。"),
        para("第四条 质量问题退款不受 7 日限制，凭照片或质检结果受理。"),
        para("第五条 以下情形不予退款：（一）定制类商品；（二）已激活的虚拟商品；（三）人为损坏。"),
        para("第三章 退款时限与金额", S_H1),
        para("第六条 一般退款在验收后 3 个工作日内原路退回；大件家电上门取件后 5 个工作日内退回。"),
        para("第七条 部分退款金额 = 成交金额 ×（1 - 折损率），折损率由质检员核定并记录原因。"),
        para("第四章 退款原因分类", S_H1),
        para("第八条 退款原因分为：质量问题、七天无理由、物流损坏、尺寸不符，共四类，"
             "录入系统时必须选择其一，不得使用“其他”。"),
        para("第五章 附则", S_H1),
        para("第九条 本制度由客服中心解释，自发布之日起施行。"),
    ]
    make_doc(story, "04_售后服务与退款管理制度.pdf")


def doc_05_formula(agg):
    yoy = (agg["y2025"] / agg["y2024"] - 1) * 100 if agg["y2024"] else 0
    story = [
        para("经营指标核算与计算公式说明", S_TITLE),
        para("——与《财务指标核算办法》配套的技术口径文档", S_NOTE),
        Spacer(1, 8),
        para("1 销售额", S_H1),
        para("销售额（Sales Amount）按订单明细逐行计算后汇总："),
        formula_png(r"S=\sum_{i} q_i \cdot p_i \cdot (1-d_i)", "f_sales.png"),
        para("其中 q 为购买数量，p 为成交单价，d 为折扣率。仅统计状态为已完成、已发货、"
             "已退款的订单；已取消订单不计入。"),
        para("2 净销售额", S_H1),
        para("净销售额等于销售额扣除同期退款金额："),
        formula_png(r"S_{net}=S-R", "f_net.png"),
        para("其中 R 为统计期内的退款总额。注意：退款的统计期间以退款发生时间（refunded_at）计，"
             "不以原订单下单时间计。"),
        para("3 同比增长率", S_H1),
        para("同比增长率用于比较本期与上年同期："),
        formula_png(r"g=\frac{S_t-S_{t-1}}{S_{t-1}}\times 100\%", "f_yoy.png"),
        para(f"示例：2025 年销售额约 {agg['y2025']} 万元，2024 年约 {agg['y2024']} 万元，"
             f"则同比增长率约为 {yoy:.1f}%。"),
        para("4 折算与舍入", S_H1),
        para("金额以人民币元为单位存储，报表展示按四舍五入保留两位小数；万元口径保留一位小数。"
             "比率类指标统一保留一位小数百分比。"),
        para("5 口径边界", S_H1),
        para("问数系统在用户未指明时间范围时，不得自行假设“去年”或“今年”，应主动向用户澄清；"
             "指代“销售额”而语境涉及退款分析时，应确认用户意指毛销售额还是净销售额。"),
    ]
    make_doc(story, "05_经营指标核算与计算公式说明.pdf")


def doc_06_annual_report(agg):
    """长文档 + 跨页表格（大于一页的表格，测试跨页续表）。"""
    regions = sorted(agg["region_q"].keys())
    story = [
        para("2025 年度经营分析报告", S_TITLE),
        para("编制部门：经营管理部 · 2026-01 · 演示数据", S_NOTE),
        Spacer(1, 8),
        para("一、全年经营概况", S_H1),
        para(f"2025 年公司实现销售额约 {agg['y2025']} 万元，同比 2024 年（{agg['y2024']} 万元）"
             f"增长约 {(agg['y2025'] / agg['y2024'] - 1) * 100:.1f}%。四季度受双11、双12 大促拉动，"
             "贡献全年最高单季销量。"),
        para("二、分月度销售额明细", S_H1),
        para("下表列出各月销售额，表格跨页时表头自动重复（续表），供文档结构恢复能力测试：", S_BODY),
    ]
    # 构造 24 行的跨页表: 各大区×上下半年逐月（用大区月度演示数据）
    conn = psycopg2.connect(host="localhost", port=5432, dbname="demo_retail",
                            user="readonly_user", password="readonly_dev")
    cur = conn.cursor()
    cur.execute("""
        SELECT r.region_name, EXTRACT(MONTH FROM o.created_at)::int AS m,
               round(sum(oi.quantity * oi.unit_price * (1 - oi.discount)) / 10000.0, 1)
        FROM order_items oi
        JOIN orders o USING (order_id)
        JOIN customers c USING (customer_id)
        JOIN regions r USING (region_id)
        WHERE o.order_status IN ('completed','shipped','refunded')
          AND o.created_at >= '2025-01-01' AND o.created_at < '2026-01-01'
        GROUP BY 1, 2 ORDER BY 1, 2""")
    mr = {}
    for region, m, amt in cur.fetchall():
        mr.setdefault(region, {})[int(m)] = float(amt)
    conn.close()
    rows = []
    for r in regions:
        for m in range(1, 13):
            rows.append([r, f"{m}月", f"{mr.get(r, {}).get(m, 0):.1f}"])
    story += data_table(["大区", "月份", "销售额(万元)"], rows,
                        caption="表 2-1 2025 年各大区逐月销售额（跨页续表，演示数据）")
    story += [
        para("三、重点品类", S_H1),
        para(f"销售额冠军为「{agg['top3'][0][0]}」（{agg['top3'][0][1]}类），全年约 "
             f"{agg['top3'][0][2]} 万元；第二名「{agg['top3'][1][0]}」约 {agg['top3'][1][2]} 万元；"
             f"第三名「{agg['top3'][2][0]}」约 {agg['top3'][2][2]} 万元。"),
        para("四、问题与改进", S_H1),
        para("西南地区全年表现低于预期，主要受物流时效影响；建议增设区域仓并优化大件配送路由。"
             "食品品类毛利率承压，需重新谈判供应链价格。"),
    ]
    make_doc(story, "06_2025年度经营分析报告.pdf")


def _scanify(pdf_path, out_path, rotate=0.6, blur=0.7, dpi=110, noise=0.35):
    """把 PDF 转为'扫描件'：降清渲染 + 微旋转 + 高斯模糊 + 加性噪声"""
    src = pymupdf.open(pdf_path)
    scan = pymupdf.open()
    for page in src:
        pix = page.get_pixmap(dpi=dpi)
        img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("L")
        img = img.rotate(rotate, expand=True, fillcolor=255, resample=Image.BICUBIC)
        img = img.filter(ImageFilter.GaussianBlur(blur))
        arr = img.load()
        w, h = img.size
        for _ in range(int(w * h * noise / 10000)):
            x, y = RNG.randrange(w), RNG.randrange(h)
            arr[x, y] = max(0, min(255, arr[x, y] + RNG.randint(-60, 60)))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=62)
        psize = (page.rect.width * 72 / dpi, page.rect.height * 72 / dpi)
        page = scan.new_page(width=psize[0], height=psize[1])
        page.insert_image(page.rect, stream=buf.getvalue())
    scan.save(out_path)
    src.close(); scan.close()


def doc_07_scan_inspection():
    """先建原生版，再转扫描风格。含可 OCR 的标题层级。"""
    story = [
        para("门店巡检记录（2025 年 12 月）", S_TITLE),
        para("巡检人：张明 · 范围：华东区 6 家门店", S_NOTE),
        Spacer(1, 8),
        para("一、巡检总体情况", S_H1),
        para("本次共巡检门店 6 家，平均得分 87.5 分。杭州西湖店得分最高（94 分），"
             "南京新街口店得分最低（76 分），主要失分项为陈列卫生与物料过期。"),
        para("二、门店得分明细", S_H1),
    ]
    rows = [["上海浦东店", "88", "陈列规范", "-"],
            ["杭州西湖店", "94", "-", "-"],
            ["苏州观前店", "89", "-", "灯光偏暗"],
            ["南京新街口店", "76", "陈列卫生", "物料过期"],
            ["无锡中山店", "85", "-", "地面湿滑"],
            ["宁波天一店", "93", "-", "-"]]
    story += data_table(["门店", "得分", "主要失分项", "其他问题"], rows)
    story += [
        para("三、整改要求", S_H1),
        para("南京新街口店须在 10 个工作日内完成整改并提交照片；其余门店保持现有标准。"),
    ]
    tmp = OUT / "_tmp_inspection_native.pdf"
    make_doc(story, "_tmp_inspection_native.pdf")
    native_dir = OUT / "native_counterparts"
    native_dir.mkdir(exist_ok=True)
    tmp.rename(native_dir / "07_门店巡检记录_2025年12月_原生版.pdf")
    _scanify(native_dir / "07_门店巡检记录_2025年12月_原生版.pdf",
             OUT / "07_门店巡检记录_2025年12月_扫描版.pdf", rotate=0.8, blur=0.8)


def doc_08_scan_qc(agg):
    n = sum(x[1] for x in agg["refund_reasons"])
    story = [
        para("产品质检报告（数码品类）", S_TITLE),
        para("报告编号：QC-DIG-2025-118 · 检测机构：演示质检中心", S_NOTE),
        Spacer(1, 8),
        para("一、抽检概况", S_H1),
        para("对声澜品牌 3 款数码产品进行季度抽检，共抽检 120 台，发现外观瑕疵 7 台，"
             "功能不良 2 台，整体不良率 7.5%。"),
        para("二、抽检结果", S_H1),
    ]
    rows = [["无线降噪耳机 Pro", "45", "3", "1", "8.9%"],
            ["真无线耳机 Air", "40", "2", "1", "7.5%"],
            ["便携蓝牙音箱", "35", "2", "0", "5.7%"]]
    story += data_table(["产品", "抽检数", "外观瑕疵", "功能不良", "不良率"], rows)
    story += [
        para("三、结论", S_H1),
        para("耳机类产品需要改进组装工序的静电防护；其余产品符合出厂标准。本报告为扫描存档版本，"
             f"共受理相关退款 {n} 笔（2025 年，演示数据）。"),
    ]
    tmp = OUT / "_tmp_qc_native.pdf"
    make_doc(story, "_tmp_qc_native.pdf")
    native_dir = OUT / "native_counterparts"
    native_dir.mkdir(exist_ok=True)
    tmp.rename(native_dir / "08_产品质检报告_数码品类_原生版.pdf")
    _scanify(native_dir / "08_产品质检报告_数码品类_原生版.pdf",
             OUT / "08_产品质检报告_数码品类_扫描版.pdf", rotate=-0.5, blur=0.9, dpi=100)


def doc_09_headphone_manual():
    story = [
        para("无线降噪耳机 Pro 产品手册", S_TITLE),
        para("型号：SL-HP90 · 品牌：声澜 · V1.3", S_NOTE),
        Spacer(1, 8),
        para("1 包装清单", S_H1),
        para("耳机主机 ×1、便携收纳袋 ×1、USB-C 充电线 ×1、说明书 ×1。"),
        para("2 基本参数", S_H1),
    ]
    rows = [["蓝牙版本", "5.3"], ["驱动单元", "40mm"], ["续航", "降噪开启约 38 小时"],
            ["充电时长", "约 1.5 小时"], ["重量", "252 g"], ["防水等级", "IPX4"]]
    story += data_table(["项目", "参数"], rows)
    story += [
        para("3 按键操作", S_H1),
        para("3.1 开关机", S_H2),
        para("长按多功能键 2 秒开机，长按 4 秒关机。"),
        para("3.2 降噪切换", S_H2),
        para("短按降噪键在“降噪开 / 通透模式 / 降噪关”之间循环切换。"),
        para("4 常见问题", S_H1),
        para("4.1 无法配对", S_H2),
        para("将耳机放入充电盒，长按功能键 6 秒直至指示灯快闪，重新搜索配对。"),
        para("4.2 单边无声", S_H2),
        para("恢复出厂设置（双击降噪键后长按 5 秒）；如仍无效，联系售后检测。"),
        para("5 保修政策", S_H1),
        para("自签收之日起整机保修 12 个月；人为损坏、进水不在保修范围。"),
    ]
    make_doc(story, "09_无线降噪耳机Pro产品手册.pdf")


def doc_10_data_dictionary():
    conn = psycopg2.connect(host="localhost", port=5432, dbname="demo_retail",
                            user="readonly_user", password="readonly_dev")
    cur = conn.cursor()
    cur.execute("""
        SELECT c.relname, a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
        WHERE n.nspname='public' AND c.relkind='r' ORDER BY c.relname, a.attnum""")
    cols = cur.fetchall()
    conn.close()
    story = [
        para("零售演示库数据字典", S_TITLE),
        para("database: demo_retail · 生成时间：2026-01", S_NOTE),
        Spacer(1, 8),
        para("本字典描述零售演示库全部表结构，供 NL2SQL 语义校验与元数据维护使用。"
             "销售额口径：quantity × unit_price × (1 - discount)，仅统计有效订单状态。", S_BODY),
        para("表清单", S_H1),
    ]
    cur_table = None
    rows = []
    for t, c, dt, notnull in cols:
        if t != cur_table:
            if rows:
                story += data_table(["字段", "类型", "非空"], rows,
                                    caption=f"表 {cur_table}")
            cur_table = t
            rows = []
        rows.append([c, dt, "是" if notnull else "否"])
    if rows:
        story += data_table(["字段", "类型", "非空"], rows, caption=f"表 {cur_table}")
    make_doc(story, "10_零售演示库数据字典.pdf")


def doc_11_faq():
    story = [
        para("客户服务话术手册（FAQ 版）", S_TITLE),
        para("客服中心 · V3.0 · 内部资料", S_NOTE),
        Spacer(1, 8),
        para("一、物流类", S_H1),
        para("问：订单显示已发货但一直没有物流更新怎么办？", S_H2),
        para("答：大件家电由专线配送，物流信息可能延迟 24-48 小时更新；若超过 48 小时，"
             "可登记工单由物流组跟进。"),
        para("问：可以指定送货时间吗？", S_H2),
        para("答：线下门店履约的大件订单支持预约送达时段，APP 内订单详情页可选择。"),
        para("二、售后类", S_H1),
        para("问：耳机进水了可以保修吗？", S_H2),
        para("答：进水属于人为损坏，不在保修范围；可申请付费维修。"),
        para("问：退款多久到账？", S_H2),
        para("答：按《售后服务与退款管理制度》，一般商品验收后 3 个工作日内原路退回，"
             "大件家电 5 个工作日内退回。"),
        para("三、会员类", S_H1),
        para("问：钻石会员有什么权益？", S_H2),
        para("答：钻石会员享全年免费上门安装、专属客服通道和大促提前购资格。"),
        para("问：积分怎么获得？", S_H2),
        para("答：每消费 1 元累计 1 积分，老带新双方各得 500 积分，积分有效期为获得之日起 12 个月。"),
    ]
    make_doc(story, "11_客户服务话术手册.pdf")


def doc_12_finance_method():
    story = [
        para("财务指标核算办法", S_TITLE),
        para("财务部 · F-2025-004 · 与零售演示库配套", S_NOTE),
        Spacer(1, 8),
        para("第一条 目的与范围", S_H1),
        para("为统一经营分析口径，制定本办法。适用于销售额、净销售额、退款金额、"
             "订单数等指标的计算与披露。"),
        para("第二条 销售额口径", S_H1),
        para("销售额 = Σ(购买数量 × 成交单价 × (1 − 折扣率))，统计订单状态为已完成、"
             "已发货、已退款的订单。已取消订单不计入销售额。"),
        para("第三条 退款口径", S_H1),
        para("退款金额以退款发生时间归属统计期间。同一订单部分退款时，按实际退款金额计，"
             "不冲减明细原始销售额；净销售额指标另行列示。"),
        para("第四条 时间归属", S_H1),
        para("销售额按订单下单时间（created_at）归属统计期间；退款按退款时间（refunded_at）归属。"
             "两者可能存在跨期差异，分析时需注明。"),
        para("第五条 币种与精度", S_H1),
        para("金额单位为人民币元，计算过程保留全精度，展示四舍五入至分；万元报表保留一位小数。"),
        para("第六条 渠道口径", S_H1),
        para("渠道以订单创建渠道为准（online/app/offline），订单状态变更不改变渠道归属。"),
    ]
    make_doc(story, "12_财务指标核算办法.pdf")


DOCS = [
    ("01_重点产品市场分析报告_2025.pdf", "原生文本", "多级目录+跨页表格，主打产品与DB联动"),
    ("02_渠道运营策略白皮书.pdf", "原生文本", "多级标题+列表+预算表"),
    ("03_2025年分区域经营数据简报.pdf", "表格密集", "三张数据表，数值与DB一致"),
    ("04_售后服务与退款管理制度.pdf", "规章制度", "编号条款"),
    ("05_经营指标核算与计算公式说明.pdf", "公式", "3个LaTeX公式+口径边界"),
    ("06_2025年度经营分析报告.pdf", "长文档", "24行跨页续表"),
    ("07_门店巡检记录_2025年12月_扫描版.pdf", "扫描件", "旋转+噪声+模糊"),
    ("08_产品质检报告_数码品类_扫描版.pdf", "扫描件", "负角度旋转+低清"),
    ("09_无线降噪耳机Pro产品手册.pdf", "产品手册", "参数表+FAQ"),
    ("10_零售演示库数据字典.pdf", "数据字典", "从DB自动生成"),
    ("11_客户服务话术手册.pdf", "FAQ", "问答体"),
    ("12_财务指标核算办法.pdf", "规章制度", "指标口径（与公式文档互补）"),
]


def main():
    print("fetching db aggregates ...")
    agg = fetch_db_aggregates()
    print("top3 2025:", agg["top3"])

    doc_01_product_report(agg)
    doc_02_channel_strategy()
    doc_03_region_table(agg)
    doc_04_refund_policy()
    doc_05_formula(agg)
    doc_06_annual_report(agg)
    doc_07_scan_inspection()
    doc_08_scan_qc(agg)
    doc_09_headphone_manual()
    doc_10_data_dictionary()
    doc_11_faq()
    doc_12_finance_method()

    manifest = {
        "generated_at": datetime.now().isoformat(),
        "note": "演示知识库。扫描件由原生 PDF 降清+旋转+噪声生成，摄取时以生成文本作为 OCR 文本层（演示代理）。",
        "documents": [
            {"file": f, "type": t, "desc": d} for f, t, d in DOCS
        ],
    }
    (OUT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"done: {len(DOCS)} documents in {OUT}")


if __name__ == "__main__":
    main()
