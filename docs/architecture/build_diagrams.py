"""Render the documentation-only TalonMart architecture atlas using stdlib SVG."""

from html import escape
from pathlib import Path
from agent_flow_content import FLOW_STEPS

OUT = Path(__file__).resolve().parent
C = {
    "ink": "#16243B", "muted": "#52647B", "line": "#9AAABE",
    "blue": "#315CDD", "blue_bg": "#EEF3FF", "teal": "#087F79",
    "teal_bg": "#EAF7F4", "orange": "#B76817", "orange_bg": "#FFF5E7",
    "purple": "#7452B7", "purple_bg": "#F3EFFB", "bg": "#F6F8FC",
}


class Diagram:
    def __init__(self, height, title, subtitle, number):
        self.height = height
        self.parts = [f'''<svg xmlns="http://www.w3.org/2000/svg" width="2400" height="{height}" viewBox="0 0 2400 {height}" role="img" aria-labelledby="title desc">
<title id="title">{escape(title)}</title><desc id="desc">{escape(subtitle)}</desc>
<defs><filter id="shadow" x="-10%" y="-10%" width="120%" height="125%"><feDropShadow dx="0" dy="5" stdDeviation="9" flood-color="#172A46" flood-opacity=".055"/></filter>''']
        for name in ("blue", "teal", "orange", "purple", "muted"):
            self.parts.append(f'<marker id="arrow-{name}" viewBox="0 0 12 12" refX="10" refY="6" markerWidth="9" markerHeight="9" orient="auto-start-reverse"><path d="M 1 1 L 10 6 L 1 11 Z" fill="{C[name]}"/></marker>')
        self.parts.append('</defs><style>text{font-family:"Microsoft YaHei","PingFang SC","Noto Sans CJK SC",Arial,sans-serif}path{stroke-linejoin:round;stroke-linecap:round}</style>')
        self.rect(0, 0, 2400, height, C["bg"], radius=0)
        self.rect(64, 56, 7, 100, C["blue"], radius=3)
        self.text(92, 70, "TALONMART  /  ARCHITECTURE ATLAS", 18, C["blue"], 700, spacing=2)
        self.text(92, 126, title, 43, C["ink"], 700)
        self.text(92, 166, subtitle, 22)
        self.text(2336, 87, f"{number}  /  03", 24, C["muted"], 600, anchor="end")
        self.text(2336, 126, "2026.09.23", 22, anchor="end")

    def rect(self, x, y, w, h, fill="white", stroke=None, radius=18, dashed=False, shadow=False):
        attrs = f'fill="{fill}" rx="{radius}"'
        if stroke:
            attrs += f' stroke="{stroke}" stroke-width="1.5"'
        if dashed:
            attrs += ' stroke-dasharray="9 7"'
        if shadow:
            attrs += ' filter="url(#shadow)"'
        self.parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" {attrs}/>')

    def text(self, x, y, value, size=22, color=None, weight=400, anchor="start", spacing=None):
        attrs = f'font-size="{size}" fill="{color or C["muted"]}" font-weight="{weight}" text-anchor="{anchor}"'
        if spacing:
            attrs += f' letter-spacing="{spacing}"'
        self.parts.append(f'<text x="{x}" y="{y}" {attrs}>{escape(value)}</text>')

    def lines(self, x, y, values, size=22, gap=34, color=None):
        for index, value in enumerate(values):
            self.text(x, y + index * gap, value, size, color)

    def label(self, x, y, value, color="muted", size=20, bg=None):
        width = sum(size if ord(ch) > 255 else size * .56 for ch in value) + 24
        self.rect(x - width / 2, y - size - 4, width, size + 14, bg or C["bg"], radius=6)
        self.text(x, y, value, size, C[color], 500, anchor="middle")

    def path(self, d, color="muted", dashed=False, arrow=True, both=False, width=3):
        attrs = f'fill="none" stroke="{C[color]}" stroke-width="{width}"'
        if dashed:
            attrs += ' stroke-dasharray="8 7"'
        if arrow:
            attrs += f' marker-end="url(#arrow-{color})"'
        if both:
            attrs += f' marker-start="url(#arrow-{color})"'
        self.parts.append(f'<path d="{d}" {attrs}/>')

    def chip(self, x, y, value, color="blue", width=None):
        width = width or sum(18 if ord(ch) > 255 else 10.3 for ch in value) + 28
        self.rect(x, y, width, 34, C[f"{color}_bg"], radius=17)
        self.text(x + width / 2, y + 24, value, 18, C[color], 600, anchor="middle")

    def panel(self, x, y, w, h, title, subtitle, color="blue", tag=None):
        self.rect(x, y, w, h, stroke="#DDE5F0", shadow=True)
        self.rect(x, y + 26, 5, 43, C[color], radius=2)
        self.text(x + 28, y + 50, title, 29, C["ink"], 700)
        self.text(x + 28, y + 84, subtitle, 20)
        if tag:
            width = sum(18 if ord(ch) > 255 else 10.3 for ch in tag) + 28
            self.chip(x + w - width - 24, y + 22, tag, color, width)

    def box(self, x, y, w, h, title, lines=(), color="blue", size=23, dashed=False, label=None):
        self.rect(x, y, w, h, C[f"{color}_bg"], C[color] if dashed else None, radius=12, dashed=dashed)
        self.text(x + 20, y + 36, title, size, C["ink"], 600)
        self.lines(x + 20, y + 70, lines, 20, 31)
        if label:
            self.text(x + w - 18, y + 34, label, 18, C[color], 600, anchor="end")

    def footer(self, y, message):
        self.path(f'M64 {y} H2336', arrow=False, width=1)
        self.text(64, y + 40, message, 19)
        self.text(2336, y + 40, "LOCAL-FIRST  ·  FACT-GROUNDED  ·  TRACEABLE", 17, C["muted"], 500, anchor="end")

    def save(self, name):
        self.parts.append('</svg>')
        (OUT / name).write_text("\n".join(self.parts), encoding="utf-8")


def overview():
    d = Diagram(2160, "TalonMart Agent · 当前项目整体架构", "用户购物与企业运营双入口，共享确定性业务事实；知识检索与文件采集各有清晰边界。", "01")
    # All connectors are routed through gutters. The web and Agent share an HTTP bus.
    d.path('M344 366 V470', 'blue', both=True)
    d.path('M1000 366 V412 H1120 V1216 H1184', 'blue')
    d.path('M1064 818 H1120', 'blue', arrow=False)
    d.path('M1556 366 V470', 'orange', both=True)
    d.path('M1556 920 V1024', 'orange')
    d.path('M344 920 V1024', 'teal', both=True)
    d.path('M564 1408 V1512', 'purple', both=True)
    d.path('M1556 1408 V1512', 'purple', both=True)
    d.path('M1928 1268 H1968 V1604 H2008', 'purple', both=True)

    d.panel(64, 220, 1000, 146, "TalonMart Web", "Vue 3 · TypeScript · Vite · Pinia", tag="购物入口")
    d.text(92, 338, "商品浏览 / 搜索 / 详情 / 购物车 / 订单 / 秒杀 / AI 模式", 23)
    d.panel(1184, 220, 744, 146, "飞书机器人 · 应用 · 多维表格", "仓储 / 采购 / 物流 / 运营", 'orange', "运营入口")
    d.text(1212, 338, "展示、协作、通知、人工确认与业务操作", 23)
    d.label(344, 424, 'HTTP / SSE', 'blue')
    d.label(886, 405, '常规购物 HTTP', 'blue')
    d.label(1556, 424, '事件 / 回复 / 表格同步', 'orange')

    d.panel(64, 470, 1000, 450, "ai-service / AImodel", "FastAPI · LangChain · LangGraph · MCP Client", tag="购物决策核心")
    d.box(92, 584, 450, 106, "购物目标与对话状态", ["抽取 / 合并 / 冲突检测 / 最少澄清"])
    d.box(582, 584, 454, 106, "意图路由与分层规划", ["Intent Router / Plan / 步骤级工具授权"])
    d.path('M542 637 H582', 'blue')
    d.box(92, 716, 450, 106, "商品决策与证据", ["硬过滤 / 确定性排序 / 比较 / 评论"])
    d.box(582, 716, 454, 106, "有界执行与事实校验", ["工具并发 / 超时 / 恢复 / 结构化回答"])
    d.text(92, 871, "会话与长期记忆 · Checkpoint · Agent Trace · 保留既有 LangChain 路径", 21)
    d.label(1108, 788, '业务工具', 'blue', 18)

    d.panel(1184, 470, 744, 450, "飞书适配与业务编排", "FastAPI adapter + n8n workflows", 'orange')
    d.box(1212, 584, 688, 100, "feishu-adapter", ["事件接入 / 多机器人 / 去重 / 回复 / read model 同步"], 'orange')
    d.path('M1556 684 V734', 'orange', both=True)
    d.label(1712, 718, 'Webhook / 同步接口', 'orange', 18, 'white')
    d.box(1212, 734, 688, 128, "n8n 部门 Workflow", ["Warehouse · Procurement · Delivery · Operations", "调用业务 API；按流程调用 ai-service 意图与摘要能力"], 'orange')
    d.text(1212, 894, "业务按钮 → 确定性 API；表格展示数据由源端同步", 20)
    d.label(1556, 983, '查询 / 操作 / 定时同步', 'orange')
    d.label(344, 983, '持久 MCP · stdio 子进程', 'teal')

    d.panel(64, 1024, 1000, 384, "独立 RAG 知识子系统", "services/ai-service/rag · 通过 MCP 公共契约调用", 'teal', "知识证据")
    d.box(92, 1140, 450, 100, "文档摄取", ["Markdown / PDF → 分块 / Transform"], 'teal')
    d.box(582, 1140, 454, 100, "混合召回", ["Dense + BM25 → RRF · 多库并行"], 'teal')
    d.box(92, 1260, 450, 100, "索引与检索资产", ["Embedding / pgvector / 本地 BM25"], 'teal')
    d.box(582, 1260, 454, 100, "重排与证据筛选", ["Reranker → Self-RAG → 引用 / Trace"], 'teal')
    d.text(92, 1390, "选购指南 / FAQ / 政策 / 品牌知识；商品实时事实走业务 API", 20)
    d.panel(1184, 1024, 744, 384, "mock-api · 确定性业务 API", "FastAPI · SQLAlchemy · psycopg", 'orange', "业务事实")
    d.box(1212, 1140, 326, 100, "电商交易", ["商品 / 评论 / 购物车 / 订单"], 'orange')
    d.box(1560, 1140, 340, 100, "运营履约", ["库存 / 采购 / 物流 / 售后"], 'orange')
    d.box(1212, 1260, 688, 100, "秒杀 · 排行榜 · fixtures 演示数据", ["状态变更经确定性接口校验；事实持久化到 PostgreSQL"], 'orange')
    d.text(1212, 1390, "飞书多维表格是业务数据投影，最终事实源在此边界内", 20)

    d.panel(2008, 220, 328, 328, "模型与外部检索", "受控依赖 / 按配置启用", 'blue')
    d.lines(2036, 346, ["LLM / Embedding", "Reranker providers", "Tavily Web Search"], 23, 45)
    d.lines(2036, 495, ["供 Agent / RAG 调用", "补充公开网页与模型能力"], 20, 30)
    d.panel(2008, 612, 328, 308, "观测与评估", "贯穿 Agent 与 RAG", 'teal')
    d.lines(2036, 742, ["Agent / RAG Trace", "Golden Set / Ragas", "Streamlit Dashboard", "Kayn / OTel 评测适配"], 21, 40)
    d.panel(2008, 1024, 328, 384, "运行与边界", "Docker Compose · 本地优先", 'purple')
    d.lines(2036, 1150, ["6 个基础服务容器", "前端由 Vite 单独启动", "RAG MCP 随 AI 进程启动", "采集与清洗在宿主机运行"], 20, 43)
    d.rect(2032, 1325, 280, 54, C['purple_bg'], radius=10)
    d.text(2172, 1360, "独立模块 ≠ 独立容器", 20, C['purple'], 600, anchor='middle')

    d.label(564, 1472, '向量 / 文档 / 运行数据', 'purple')
    d.label(1556, 1472, '业务事实读写', 'purple')
    d.panel(64, 1512, 1864, 184, "PostgreSQL · 持久化底座", "共享实例，按业务、Agent 与 RAG 的逻辑所有权分区；含 pgvector / pg_search 扩展。", 'purple')
    for x, title, desc in [(92,'业务数据','商品 / 订单 / 库存 / 采购 / 秒杀'), (690,'Agent 状态与追踪','会话 / 目标 / 记忆 / Checkpoint / Trace'), (1350,'RAG 子系统数据','文档 / Chunk / 向量 / 评估 / Trace')]:
        d.text(x, 1642, title, 22, C['ink'], 600)
        d.text(x, 1674, desc, 20)
    d.panel(2008, 1512, 328, 184, "Redis", "原子计数与缓存", 'purple')
    d.lines(2036, 1642, ["秒杀配额 / 扣减补偿", "排行榜缓存"], 21, 32)

    d.text(64, 1762, "独立文件流水线", 28, C['ink'], 700)
    d.text(334, 1762, "RPA / data-ops · CSV 契约连接采集与处理，当前交付止于文件", 22)
    for x, w, title, body in [
        (64, 486, '01  授权网页 → URL 发现', ['Playwright 分类 / 搜索页发现', 'SKU 规范化 / 去重 → 输入 CSV']),
        (626, 486, '02  商品详情采集', ['影刀 RPA → 统一原始 CSV', '可替换采集器 / 统一 CSV 数据契约']),
        (1188, 486, '03  pandas / data-ops', ['契约校验 / 字段清洗 / 价格规范化', 'processor 注册 / 批次管理 / 断点续跑']),
        (1750, 586, '04  文件交付', ['标准化 CSV / 失败 CSV', 'manifest / pipeline result / 异常证据'])]:
        d.box(x, 1804, w, 146, title, body, 'teal')
    for x in (550,1112,1674):
        d.path(f'M{x} 1877 H{x+76}', 'teal')
    d.text(64, 1995, "事实边界：Agent 调工具获得业务事实；RAG 提供引用知识；飞书维护 read model；RPA 文件入库需后续独立设计。", 23, C['ink'], 500)
    d.text(64, 2038, "阅读顺序：图 01 理解整体系统；图 02 理解 Agent 与 RAG 的完整协作；图 03 逐步拆解购物决策过程。", 20)
    d.footer(2074, "当前系统总览 · 主调用方向、业务事实来源与运行形态。")
    d.save('01-system-overview.svg')


def decision_detail():
    d = Diagram(3220, 'Agent × RAG · 完全体目标架构', '以购物目标驱动决策，融合授权画像、商品知识、实时事实与检索证据，形成反馈学习闭环。', '02')
    # Three coordinated columns: state/catalog, online decisions, knowledge/actions.
    d.panel(696, 220, 1016, 2048, '购物决策 Agent · 在线主链', 'API / SSE + 单 Agent 编排 · 按计划选择、并行或跳过步骤', tag='完整设计')
    steps = [
        (352, 108, '01  API Consumer → HTTP / SSE Adapter', ['用户问题 / 页面引用 / 候选 ID / 聚合行为信号；版本化输入']),
        (510, 108, '02  Identity Context + Consent Policy Gate', ['主体隔离 / purpose / scope / 撤回与过期；无授权走非个性化']),
        (668, 150, '03  Context Assembler + Proactive Policy', ['服务端事实 / 页面 / 会话 / 授权画像 → 带来源与预算的上下文', '主动建议按规则、冷却与免打扰生成；用户接入后启动完整决策']),
        (870, 132, '04  Shopping Goal Manager', ['目标抽取 / 多轮合并 / 冲突检测 / 最少澄清 / goal_version', '本轮硬约束与明确偏好优先；长期偏好只提供受控软信号']),
        (1054, 170, '05  Intent Router → Planner → Step Policy Gate', ['路由 / 执行 DAG / 依赖 / 工具白名单 → Bounded Executor', '有界并发 / 超时 / 重试 / 取消 / 失败恢复；工具逐步授权', '商品 / 评论 / 购物车 / Web / RAG 通过受控工具接口调用']),
        (1276, 170, '06  Multi-route Recall · 多路召回与融合', ['lexical_search / semantic_search / graph_relation', 'behavior_affinity / category_popular → 配额或 RRF 融合', 'canonical ID 去重 / lane 来源 / 索引版本 / 全局候选预算']),
        (1498, 132, '07  Commerce Fact Gateway → Hard Filter', ['批量补全价格 / 促销 / 库存 / 配送 → 同一 fact_snapshot_id', 'fresh / stale / unknown / conflict；校验预算、品牌与必需规格']),
        (1682, 170, '08  Personalized Ranker + Safety Blend', ['注册模型批量排序 → 后置硬约束复核 → 多样性 / 去重', '保留 baseline / model / 最终顺序与可解释原因代码', '关闭个性化、无授权、超时、版本异常 → 确定性 baseline']),
        (1904, 132, '09  Comparator / Review Insights → Grounding Verifier', ['对比矩阵 / 评论观点 / 知识引用 → 事实、约束与证据校验', '结论绑定同一事实快照；一次有界恢复，失败返回安全降级']),
        (2088, 132, '10  Structured Response Composer → API / SSE', ['推荐 / 比较 / 澄清 / action_preview / fallback + 来源证据', 'request_id / sequence / 幂等 done / 历史恢复 / 兼容文本']),
    ]
    for i, (y,h,title,body) in enumerate(steps):
        if i:
            previous = steps[i-1]
            d.path(f'M1204 {previous[0]+previous[1]} V{y}', 'blue')
        d.box(724,y,960,h,title,body,'blue',24)
    d.text(724, 2250, '在线领域：personalization / catalog / recommendation；统一基础设施适配。', 20)

    d.panel(64, 220, 536, 568, '授权画像与实时特征', 'personalization domain', 'purple')
    d.box(92, 344, 480, 134, 'Explainable User Profile', ['显式 / 推断 / 临时偏好分层', '置信度 / 衰减 / 证据量 / profile_version'], 'purple')
    d.path('M332 532 V478', 'purple')
    d.box(92, 532, 480, 190, 'Feature Registry + Online Store', ['共享特征定义 / 在线低延迟快照', 'purpose 校验 / TTL / 撤回立即阻断', '画像仅提供软特征；读取失败安全回退'], 'purple')
    d.text(92, 758, '授权检查覆盖事件、画像、特征与训练出口', 20)
    d.path('M572 410 H616 V742 H724', 'purple')
    d.label(660, 727, '授权画像', 'purple', 18)

    d.panel(64, 860, 536, 364, '购物状态与会话记忆', 'Goal Repository + LangGraph Checkpoint', 'blue')
    d.box(92, 974, 480, 194, '按用户 / 会话隔离的状态', ['ShoppingGoal / goal_version / 历史消息', '长期明确偏好与本轮目标分别持久化', '会话续接 / 幂等状态迁移 / Trace 关联'], 'blue')
    d.path('M572 988 H724', 'blue', both=True)
    d.label(660, 968, '目标状态', 'blue', 18)
    d.text(92, 1199, '用户本轮需求与页面上下文共同驱动计划', 20)

    d.panel(64, 1300, 536, 944, '商品知识与权威事实', 'catalog / recommendation', 'orange')
    d.box(92, 1420, 480, 132, 'Product Ontology / Entity Resolution', ['SPU / SKU / offer · 属性规范与版本', '来源映射 / 实体消歧 / 质量隔离'], 'orange', 21)
    d.path('M332 1552 V1604', 'orange')
    d.box(92, 1604, 480, 132, 'Product Graph + 商品召回索引', ['受控图查询 / 关系证据 / canonical ID', '独立商品 embedding / 版本化索引'], 'orange')
    d.text(92, 1781, '商品语义索引与 RAG 文档 Chunk 索引分开维护', 19, C['orange'], 500)
    d.path('M572 1668 H620 V1390 H724', 'orange')
    d.label(660, 1375, '图 / 索引', 'orange', 18)
    d.box(92, 1824, 480, 164, '权威来源适配器 + Fact Cache', ['mock-api / 商品库 / 受控 RPA 数据契约', 'catalog / price / promotion / inventory', 'delivery · 来源优先级 / 时效 / 熔断'], 'orange', 22)
    d.path('M572 1906 H648 V1564 H724', 'orange')
    d.label(659, 1543, '实时事实', 'orange', 18)
    d.box(92, 2024, 480, 164, '一致的 CommerceFactSnapshot', ['SKU / seller / region / quantity / as_of', '可追溯来源 / 证据 / fresh_until', '排序、比较与回答消费同一决策快照'], 'orange', 22)
    d.text(92, 2220, '业务事实由权威服务拥有；Agent 保存决策快照', 19)

    d.panel(1808, 220, 528, 1216, '独立 RAG 知识服务', 'MCP 公共契约 · 持久 stdio Client', 'teal')
    rag = [
        (352, '01  原始问题 + collections', ['内部 Query Processing / Intent Router', '按需改写 / 扩展；保留原问题绑定']),
        (520, '02  多知识库并行混合召回', ['Dense + BM25 → RRF / 元数据过滤', 'collection 并发、限时与部分失败隔离']),
        (688, '03  Reranker → Self-RAG Judge', ['相关性 / 充分性 / 证据筛选', 'CrossEncoder / Qwen / LLM 可插拔']),
        (856, '04  Evidence Response', ['知识上下文 / 引用 / query_trace', '选购指南 / FAQ / 政策 / 品牌知识']),
    ]
    for i,(y,title,body) in enumerate(rag):
        if i:
            d.path(f'M2072 {rag[i-1][0]+126} V{y}', 'teal')
        d.box(1836,y,472,126,title,body,'teal',23)
    d.box(1836, 1040, 472, 302, '摄取与文档索引', ['Markdown / PDF → Loader / Chunker', 'Transform：Caption / 去噪 / Rewrite', 'Embedding → pgvector + BM25', '文档 / Chunk / 来源 / 版本 / 去重', 'LLM / Embedding / Rerank 工厂接口', 'Ingestion Trace / Query Trace'], 'teal')
    d.text(1836, 1405, '知识证据进入解释与校验；实时商品事实走网关', 19)
    d.path('M2308 1232 H2322 V584 H2308', 'teal')
    d.path('M1684 1134 H1740 V416 H1836', 'teal')
    d.label(1748, 1118, 'MCP 请求', 'teal', 18)
    d.path('M1836 932 H1772 V1970 H1684', 'teal')
    d.label(1750, 1948, '引用证据', 'teal', 18)

    d.panel(1808, 1496, 528, 274, '工具与模型依赖', '统一授权、限时、错误与 Trace 契约', 'blue')
    d.lines(1836, 1620, ['LLM：理解 / 规划 / 基于证据的解释', '商品 / 评论 / 购物车 / 订单：业务工具', 'Tavily：受控公开网页搜索', 'Embedding / Reranker：按配置调用'], 21, 37)

    d.panel(1808, 1830, 528, 414, '确认式动作闭环', '动作预览 / 用户确认 / 加入购物车', 'orange')
    d.box(1836, 1946, 472, 118, 'Action Guard / 独立确认 API', ['预览 → 用户确认 → 令牌 / 幂等校验', '重查价格、库存；条件变化则重新预览'], 'orange', 23)
    d.path('M2072 2064 V2116', 'orange')
    d.label(2220, 2097, '有效确认后', 'orange', 18, 'white')
    d.box(1836, 2116, 472, 100, 'mock-api /cart → 权威结果', ['写入至多一次；记录 executed 审计'], 'orange')
    d.path('M1684 2140 H1740 V2024 H1836', 'orange', both=True)
    d.label(1744, 2178, '预览 / 确认', 'orange', 18)

    # Closed loops use the outer gutters, away from the decision spine.
    d.path('M1204 2268 V2318 H660 V2360', 'purple')
    d.label(1040, 2300, '消费方真实回执 / 服务端可信事件', 'purple', 20)
    d.path('M280 2360 V2288 H28 V626 H92', 'purple')
    d.label(252, 2274, '在线特征 / 画像更新', 'purple', 18)
    d.path('M2336 2610 H2372 V1806 H1684', 'purple', dashed=True)
    d.label(2064, 1793, '版本化模型 / 受控切换', 'purple', 18)

    d.panel(64, 2360, 2272, 420, '反馈 → 特征 → 训练 → 受控发布', 'workers / ml 独立于在线 Web 进程运行；训练、回填与索引构建异步执行。', 'purple', '持续优化闭环')
    offline = [
        (92, '01  Feedback API / Event Store', ['认证 / Consent Gate / 有效曝光回执', 'render_token → impression_rendered', '购买 / 退货来自服务端可信适配器', 'append-only / outbox / 去重 / 隔离']),
        (664, '02  Event Worker / Feature Store', ['乱序 / 迟到 / 重试 / DLQ / 回放', '共享特征定义 → Online / Offline', '时间窗 / TTL / 版本 / 授权数据血缘', '撤回、删除传播到特征与训练候选']),
        (1236, '03  PIT 数据集 / 排序训练', ['model_training 授权独立校验', '真实曝光 / 成熟标签 / 时间切分', '数据质量校验 → 可审计排序训练', '固定评测集 / 分群指标 / 数据集血缘']),
        (1808, '04  Model Registry / Release', ['hash / signature / 版本兼容 manifest', '模型与数据血缘 / Approved 制品', 'Shadow → 审批与受控启用', '指标门禁 / kill switch / baseline 回退']),
    ]
    for x,title,body in offline:
        d.box(x,2490,500,216,title,body,'purple',21)
    for x in (592,1164,1736):
        d.path(f'M{x} 2600 H{x+72}', 'purple')
    d.text(92, 2745, '独立索引构建与增量更新：商品语义索引 / 行为共现 / 品类热门；manifest 校验、原子切换与回滚。', 22)

    d.panel(64, 2860, 2272, 224, '贯穿全链路的治理与运行底座', '授权、事实、特征、索引和模型版本共同进入可重放的决策证据。', 'purple')
    cols = [
        (92, 'Trace 与审计', ['Agent / RAG / 工具 / 反馈关联', 'fact / profile / feature / model 版本']),
        (668, '评测与发布门禁', ['Golden Set / Ragas / 排序质量 / 性能', '事实准确率 / 分群表现 / 稳定性护栏']),
        (1244, '安全与可恢复性', ['内容信任分层 / URL 安全 / 最小授权', '用户隔离 / 脱敏 / 硬约束 / 自动回退']),
        (1820, '持久化与制品', ['PostgreSQL / pgvector / 缓存适配', '图存储 / 事件 / 特征 / 索引 / 模型制品']),
    ]
    for x,title,body in cols:
        d.text(x, 3000, title, 23, C['ink'], 600)
        d.lines(x, 3034, body, 20, 30)
    d.text(64, 3140, '图例：蓝色 = 在线决策；橙色 = 商品事实与确认动作；绿色 = RAG 知识；紫色 = 数据与模型闭环。虚线 = 受控制品更新。', 21)
    d.footer(3162, '完整目标设计 · 在线决策、知识证据、实时事实与数据学习各司其职。')
    d.save('02-agent-rag-flow.svg')


def agent_flow_detail():
    d = Diagram(4790, 'Agent 链路拆解 · 一次购物决策如何完成', '与图 02 的十个在线节点逐一对应：从输入与处理，到输出与分支，展开每一步的实际职责。', '03')
    d.panel(64, 220, 2272, 184, '贯穿示例 · 从一句购物需求开始', '沿着需求理解、商品检索、事实核实、对比解释与结果交付，观察数据如何逐步转化。', tag='10 步 / 30 个处理环节')
    d.text(96, 355, '“预算 3000 元以内，通勤用降噪耳机，比较两款并说明理由。”', 32, C['ink'], 600)
    d.text(64, 465, '阅读方式', 23, C['ink'], 700)
    d.text(196, 465, '沿左侧编号向下阅读；每行从左到右为 输入 → 三个处理环节 → 输出；底部说明分支与异常处理。', 22)
    d.text(64, 503, '执行方式', 23, C['ink'], 700)
    d.text(196, 503, '编号表达逻辑顺序；实际由计划按意图选择、跳过或并行执行，澄清和失败可提前返回。', 22)

    for index, step in enumerate(FLOW_STEPS):
        y = 548 + index * 360
        color = step['color']
        if index < len(FLOW_STEPS) - 1:
            d.path(f'M100 {y+80} V{y+358}', color, width=3)
        d.parts.append(f'<g id="flow-step-{index+1:02d}">')
        d.rect(66, y+6, 68, 68, C[color], radius=22)
        d.text(100, y+51, f'{index+1:02d}', 30, 'white', 700, anchor='middle')
        d.rect(160, y, 2176, 324, stroke='#DDE5F0', shadow=True)
        d.rect(160, y+24, 5, 42, C[color], radius=2)
        d.text(188, y+44, step['title'], 28, C['ink'], 700)
        d.text(2308, y+43, step['owner'], 20, C[color], 500, anchor='end')
        d.box(188, y+78, 380, 158, '输入', step['input'], color, 22)
        for j, (title, lines) in enumerate(step['operations']):
            x = 608 + 392*j
            d.box(x, y+78, 368, 158, f'{j+1}  {title}', lines, color, 23)
        d.box(1824, y+78, 484, 158, '输出', step['output'], color, 22)
        for x1,x2 in ((568,608),(976,1000),(1368,1392),(1760,1824)):
            d.path(f'M{x1+3} {y+157} H{x2-5}', color, width=2)
        d.path(f'M188 {y+258} H2308', arrow=False, width=1)
        d.chip(188, y+274, '分支处理', color, 110)
        d.text(320, y+298, step['branch'], 20)
        d.parts.append('</g>')

    d.text(64, 4200, '三条配合主链运行的协作链路', 30, C['ink'], 700)
    d.text(64, 4240, 'RAG 补充知识证据；动作由用户确认后执行；数据与模型在请求之外持续更新。', 22)
    support = [
        (64, '知识证据链', '05 发起查询 → 09 使用与校验', 'teal', [
            ('绑定问题与知识库', '原始问题 + collections；内部改写与扩展'),
            ('混合召回与融合', '各知识库 Dense / BM25 → RRF'),
            ('重排与充分性判断', 'Reranker → Self-RAG → 筛选证据'),
            ('返回知识与引用', '上下文 + 引用 + Trace；供解释与核验'),
        ], '商品索引与文档索引分开；知识不替代实时事实。'),
        (832, '确认式加购链', '10 输出预览 → 独立确认接口', 'orange', [
            ('用户查看并确认', '商品 / 数量 / 价格条件组成动作预览'),
            ('校验确认凭证', '绑定主体、会话、动作、有效期与幂等键'),
            ('重查价格与库存', '若条件变化，生成新预览并重新确认'),
            ('执行并返回权威结果', '/cart 写入至多一次；记录执行审计'),
        ], '对话生成预览与业务写入使用不同调用入口。'),
        (1600, '反馈与学习链', '10 展示回执 → 02 / 03 / 06 / 08', 'purple', [
            ('接收真实行为', '有效曝光、点击；购买与退货使用可信源'),
            ('异步处理与特征更新', '去重 / outbox / worker → 在线与离线特征'),
            ('构建样本并评测', '独立训练授权 / PIT 样本 / 时间切分'),
            ('受控发布新模型', '注册制品 / Shadow / 审批 / 开关与回退'),
        ], '在线请求只读取特征与模型；训练和回填异步执行。'),
    ]
    for x,title,subtitle,color,items,note in support:
        d.panel(x, 4292, 736, 388, title, subtitle, color)
        for j,(heading,description) in enumerate(items):
            yy = 4420 + j*61
            d.rect(x+28, yy-20, 30, 30, C[f'{color}_bg'], radius=9)
            d.text(x+43, yy+2, str(j+1), 18, C[color], 600, anchor='middle')
            d.text(x+76, yy, heading, 22, C['ink'], 600)
            d.text(x+76, yy+27, description, 20)
        d.text(x+28, 4656, note, 19, C[color])
    d.footer(4720, '完整目标设计 · 贯穿标识：request / goal / fact / feature / model / trace。')
    d.save('03-agent-flow-breakdown.svg')


if __name__ == '__main__':
    overview()
    decision_detail()
    agent_flow_detail()
    template = (OUT / 'atlas.template.html').read_text(encoding='utf-8')
    for i, name in enumerate(('01-system-overview.svg', '02-agent-rag-flow.svg', '03-agent-flow-breakdown.svg'), 1):
        svg = (OUT / name).read_text(encoding='utf-8')
        # Each embedded diagram gets unique IDs so markers resolve within its view.
        for old in ('title', 'desc', 'shadow', 'arrow-blue', 'arrow-teal', 'arrow-orange', 'arrow-purple', 'arrow-muted'):
            svg = svg.replace(f'id="{old}"', f'id="{old}-{i}"').replace(f'url(#{old})', f'url(#{old}-{i})')
        svg = svg.replace('aria-labelledby="title desc"', f'aria-labelledby="title-{i} desc-{i}"')
        template = template.replace(f'__SVG_{i}__', svg)
    (OUT / 'index.html').write_text(template, encoding='utf-8')
    print('Generated 3 architecture SVGs in', OUT)
