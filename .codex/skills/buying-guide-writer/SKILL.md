---
name: buying-guide-writer
description: Create high-quality Chinese product buying guides in Markdown. Use when the user asks to write, generate, expand, rewrite, audit, or standardize a 商品选购指南, buying guide, purchase guide, brand/model comparison, category recommendation document, or RAG-ready product knowledge document. The skill enforces category-specific writing, anti-template quality gates, scenario recommendations, brand/model coverage, adaptive parameter tables, and post-generation quality checks.
---

# Buying Guide Writer

## Workflow

1. Identify the product category, target audience, and usage context. If the category is unclear, ask one concise clarification question.
2. Use `references/template.md` as the default heading structure, but adapt the substance of every section to the category. The template is a structure aid, not reusable prose.
3. Browse and cross-check recent sources when the user asks for current brands, rankings, prices, real models, or detailed brand coverage.
4. Cover mainstream brands, high-end brands, value brands, and important niche brands when the category supports them.
5. For each recommendation, state who it fits, who should avoid it, why it fits, and the concrete risk or tradeoff.
6. Keep headings stable for Markdown/RAG splitting: `#` title, `##` major sections, and `###` sub-sections.
7. Prefer dense Markdown tables for comparisons, but use paragraphs for reasoning, tradeoffs, pitfalls, and final recommendations.
8. After writing, run a quality pass and a review pass. Report review findings, fix actionable issues, re-check the affected sections, and only then present the final result.

## Hard Quality Rules

- Do not judge quality by word count alone, but a full buying guide should usually be at least 5000 Chinese characters unless the user asks for a short version.
- Do not pad content to meet length. Add only useful decision logic, scenario detail, model comparison, maintenance cost, pitfalls, and checklists.
- Do not use low-quality template phrases such as `成熟配置`, `主流可用`, `场景增强`, `按具体规格确认`, `按具体型号确认`, `核心性能或材料`, `长期成本参数`, or `决定主要体验上限`.
- Do not create large repeated phrasing across sections. Avoid repeating the same sentence shape in `核心参数解释`, `核心选购维度`, `典型场景推荐`, and `最终推荐购买`.
- Do not cross-contaminate categories. For example, non-appliance guides must not mention power, water pipes, installation openings, filters, noise, or appliance maintenance unless the category truly needs them.
- Do not copy phone-specific, appliance-specific, pet-specific, skincare-specific, or furniture-specific fields into unrelated categories.
- Do not leave `典型场景推荐` as one-line filler. Each scenario must include concrete recommendation direction, reason, suitable user, and risk.
- Do not leave `最终推荐购买` as short brand lists. It must recommend configuration/model directions first, then include concrete brand or series examples, who should choose them, why, and what to avoid.
- Do not concatenate brand and model names. Use readable names such as `Ninja Dual Zone`, not `NinjaDual Zone`.
- Do not invent exact prices, release dates, rankings, or specifications when current accuracy matters; browse or clearly mark uncertainty.

## Required Sections

- `## 1. 快速选购结论`: Include user personas, fast decisions, and priority dimensions.
- `## 2. 品类基础知识`: Explain what the product solves, what it does not solve, and how users should think before comparing brands.
- `## 3. 品牌与产品线梳理`: Cover enough brand breadth for the category and explain each brand/series positioning.
- `## 4. 预算与取舍`: Explain what is worth paying for, what is marketing, and what should not be sacrificed.
- `## 5. 核心选购维度`: Use category-specific dimensions and varied reasoning, not repeated boilerplate.
- `## 6. 典型场景推荐`: Provide at least three concrete scenarios with recommendation direction, reason, suitable user, and risk.
- `## 7. 详细型号参数对比`: Use columns that match the category attributes.
- `## 8. 常见误区与避坑`: Use real category pitfalls, not generic `忽略安装维护` unless installation is truly central.
- `## 9. 购买检查清单`: Use category-specific checklist items and concrete pass/fail standards.
- `## 10. 最终推荐购买`: Provide actionable final recommendations, not short brand summaries.

## Post-Generation Quality Gate

After generating or rewriting a guide:

1. Sample-read at least `快速选购结论`, `典型场景推荐`, `详细型号参数对比`, `常见误区`, and `最终推荐购买`.
2. Search or visually inspect for template filler, repeated sentence patterns, brand/model concatenation, and category contamination.
3. Check whether each table column is category-specific.
4. Check whether every scenario has a real risk point.
5. Check whether final recommendations explain configuration, fit, reason, avoid conditions, and concrete brand/series examples.
6. Run a review pass after every newly generated guide or batch of guides. The review must explicitly check compliance with this skill: structure completeness, category-specific tables, template filler, repeated phrasing, category contamination, vague non-actionable advice, final recommendation usefulness, and RAG-friendly headings/paragraphs.
7. Treat review findings as actionable defects. Fix them before final response unless the user explicitly asks for review-only output.
8. Re-check edited sections after fixes.
9. In the final response, include a concise "审查发现" summary and a "修复总结" summary. If no actionable issues remain, state that clearly.


## High-Quality Writing Heuristics

- Build the category decision model before listing brands. Example: air conditioners use `space load -> installation conditions -> efficiency/inverter behavior -> comfort -> service`.
- Identify non-negotiable preconditions before recommendations. Examples: air conditioners require outdoor unit position and power; mattresses require sleep position and trial policy; pet food requires life stage and transition plan.
- Write scenarios like a real sales advisor: recommendation direction, reason, suitable user, and risk must all be concrete.
- Final recommendations must include both configuration direction and brand/series examples. Do not output only one of them.
- Explain tradeoffs, not only parameter definitions. Say when a parameter matters, when it is overkill, and what it can sacrifice.
- Include what the product cannot solve or should not be expected to do.
- Quality review must include semantic reading of key sections, not only keyword scanning.
- Review output must distinguish findings from fixes: list actionable issues found, then list what was changed or state that no changes were needed.

## Output Rules

- Write in Chinese unless the user requests another language.
- Title format: `#{商品品类}选购指南`.
- Include a fast decision section near the top.
- Include a brand and model section with enough breadth for the category.
- Include a detailed parameter comparison table whose columns match the category attributes.
- Avoid generic filler such as `根据个人需求选择` unless followed by concrete criteria.
- Keep the document useful for RAG ingestion: headings should be meaningful, paragraphs should be self-contained, and tables should not rely on hidden context.

## Table Adaptation Examples

- 手机: 芯片, 屏幕, 电池, 影像, 系统, 快充, 重量, 价格区间.
- 冰箱: 容量, 制冷方式, 循环系统, 能效等级, 分区, 除菌净味, 尺寸, 噪音.
- 耳机: 佩戴方式, 降噪, 续航, 编码, 延迟, 防水, 麦克风, 连接稳定性.
- 洗衣机: 容量, 电机, 洗净比, 烘干方式, 除菌, 能效, 噪音, 筒径.
- 路由器: Wi-Fi 标准, 频段, 速率, 天线/射频, Mesh, 端口, 覆盖面积, 并发设备.
- 空调: 匹数/制冷量, APF/能效, 低频变频, 送风舒适度, 制热能力, 噪音, 安装条件, 售后安装费用.
- 空气炸锅: 容量, 炸篮底面积, 热风结构, 温控范围, 清洁结构, 涂层材质, 配件售后.

## Reference

Read `references/template.md` when generating a full guide or when the user asks for the reusable template.

