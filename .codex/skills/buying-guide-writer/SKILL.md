---
name: buying-guide-writer
description: Create structured Chinese product buying guides in Markdown. Use when the user asks to write, generate, expand, or standardize a 商品选购指南, buying guide, purchase guide, brand/model comparison, or category recommendation document for consumer products. The skill produces a reusable guide structure with quick conclusions, user personas, brand/model coverage, adaptive parameter tables, scenario recommendations, pitfalls, and purchase checklists; browse when the guide needs current brands, rankings, prices, or real model data.
---

# Buying Guide Writer

## Workflow

1. Identify the product category and target audience. If the category is unclear, ask one concise clarification question.
2. Use `references/template.md` as the default Markdown structure.
3. Adapt every table field to the product category. Do not reuse phone-specific parameters unless the product is a phone.
4. When the user asks for current brands, rankings, prices, real models, or detailed brand coverage, browse and cross-check recent sources before writing.
5. Cover mainstream brands, high-end brands, value brands, and important niche brands when the category supports them.
6. For each recommendation, state who it fits, who should avoid it, and the reason.
7. Keep headings stable so the output can be ingested by a Markdown/RAG splitter: `#` for the document title, `##` for major sections, and `###` for sub-sections.
8. Prefer dense Markdown tables for model comparisons, but keep paragraphs for buying logic and caveats.

## Output Rules

- Write in Chinese unless the user requests another language.
- Title format: `#{商品品类}选购指南`.
- Include a fast decision section near the top.
- Include a brand and model section with enough breadth for the category.
- Include a detailed parameter comparison table whose columns match the category attributes.
- Avoid generic filler such as "根据个人需求选择" unless followed by concrete criteria.
- Do not invent exact prices, release dates, rankings, or model specifications when current accuracy matters; browse or clearly mark uncertainty.
- If browsing is not available and the user requires current market data, explain that current verification is needed before finalizing.

## Table Adaptation Examples

- 手机: 芯片, 屏幕, 电池, 影像, 系统, 快充, 重量, 价格区间.
- 冰箱: 容量, 制冷方式, 循环系统, 能效等级, 分区, 除菌净味, 尺寸, 噪音.
- 耳机: 佩戴方式, 降噪, 续航, 编码, 延迟, 防水, 麦克风, 连接稳定性.
- 洗衣机: 容量, 电机, 洗净比, 烘干方式, 除菌, 能效, 噪音, 筒径.
- 路由器: Wi-Fi 标准, 频段, 速率, 天线/射频, Mesh, 端口, 覆盖面积, 并发设备.

## Reference

Read `references/template.md` when generating a full guide or when the user asks for the reusable template.
