# 影刀 RPA CSV 交付契约

本目录保存 TalonMart 的影刀流程说明和人工验收材料。影刀负责把已授权网页中的可见信息交付为原始 CSV，`services/data-ops` 负责后续校验和标准化。两者只通过文件契约协作，不共享页面选择器、账号状态或数据库连接。

## 能力边界

- `WebPageExportContract` 固定通用输入循环、输出列顺序、页面状态和错误代码，具体站点只能追加字段。
- `DatasetContract` 定义 pandas processor 所需列、字段类型、唯一性规则以及标准/失败文件名。
- `ProcessorContract` 接收内存表格和 `DatasetContract`，返回标准行、失败行与对账统计。
- `RuntimeDirectoryContract` 只定义运行目录职责，不主动创建目录或移动文件。
- 通用影刀模板不包含京东 CSS/XPath、京东页面状态或京东字段解析；这些内容属于站点实现。

阶段 J 只建设文件流水线：不新增数据库表，不修改 `items` 表结构或数据，不执行数据库 DDL 或写入，也不调用 mock-api、Operations Workflow 或飞书 read model。

## 通用原始 CSV

所有站点的原始 CSV 必须先包含以下固定列，站点字段只能追加在这些列之后：

| 列 | 类型 | 语义 |
| --- | --- | --- |
| `dataset_type` | string | processor 路由键，使用小写 snake_case |
| `batch_id` | string | 一次输入清单执行的稳定批次标识 |
| `input_index` | integer | 输入行序号；失败行也必须保留 |
| `source_url` | url | 本行实际访问的已授权来源页面 |
| `captured_at` | datetime | 页面状态被采集时的 ISO 8601 UTC 时间 |
| `crawl_status` | string | `success`、`partial` 或 `failed` |
| `error_code` | string | 成功时为空；其他状态使用稳定错误代码 |

通用错误代码包括：

| 错误代码 | 含义 |
| --- | --- |
| `invalid_input` | 输入缺失或格式无效 |
| `navigation_failed` | 页面无法打开 |
| `page_timeout` | 等待页面就绪超时 |
| `field_missing` | 页面可访问但必要字段缺失 |
| `manual_verification_required` | 登录或验证码需要人工处理 |
| `access_restricted` | 页面权限或访问策略限制 |
| `adapter_error` | 站点适配子流程发生未分类错误 |

影刀必须为每个输入行输出且只输出一行结果。`partial` 和 `failed` 行不得静默丢弃；遇到登录、验证码或访问限制时停止自动化并保留现场，不实现绕过逻辑。账号、密码、Cookie、Token 和验证码内容不得进入 CSV、日志、截图文件名或仓库。

## `jd_product` 首个实现

提交到仓库的输入样例是 `fixtures/rpa/jd_product_urls.csv`，只包含：

| 列 | 语义 |
| --- | --- |
| `input_index` | 与原始 CSV 对账的输入序号 |
| `product_url` | 京东商品页地址；仓库 fixture 使用保留的 `.invalid` 域名 |

`jd_product` 在通用列后追加以下站点列：

| 列 | 语义 |
| --- | --- |
| `jd_sku_id` | 从输入 URL 或当前商品页解析的 SKU |
| `title` | 采集时页面可见商品标题 |
| `display_price` | 采集时、当前页面和登录状态下的原始展示价格文本，不代表长期或全部地区价格 |
| `shop_name` | 页面可见店铺名称 |
| `primary_image_url` | 页面可见主图地址 |
| `capture_region` | 预留的采集地区字段；第一版不录制页面地区元素，固定留空 |

对应原始输出样例为 `fixtures/rpa/jd_product_export.csv`。fixture 同时覆盖 `success`、`partial` 和 `failed`，所有 URL、商品、店铺、价格和时间均为合成数据。人工运行影刀时应在本地输入文件中替换为已获授权的真实 URL，不得把真实会话信息提交到仓库。

## pandas processor 交付

`JD_PRODUCT_DATASET_CONTRACT` 要求原始 CSV 完整声明通用列和京东站点列，并使用 `batch_id + input_index` 作为输入行唯一键。字段存在不代表字段值一定非空；第一版 `capture_region` 固定为空，`partial` 或 `failed` 的其他空值与业务规则由 `jd_product` processor 在后续任务中处理。

`ProcessorContract.process(frame, contract)` 返回：

- `normalized_rows`：通过校验并完成标准化的行。
- `failed_rows`：保留 `input_index`、`source_url`、`crawl_status` 和 `error_code` 的失败行。
- `summary`：输入、标准、失败和重复行数等整数对账指标。

标准输出文件模板为 `{dataset_type}_{batch_id}_normalized.csv`，失败输出文件模板为 `{dataset_type}_{batch_id}_failed.csv`。

## 影刀通用模板应用

J2 的影刀应用名称统一为 `TalonMart - Web Page to CSV`，完整搭建契约见 [`templates/web-page-to-csv.md`](templates/web-page-to-csv.md)。应用包含一个 Main 流程和五个稳定子流程：

- `LoadInputRows`：读取输入 CSV，并产生稳定 `input_index`。
- `InvokeSiteAdapter`：按 `dataset_type` 路由到站点适配流程。
- `AppendExportRow`：保证每个输入行恰好追加一条结果。
- `ExportRawCsv`：按固定通用列和站点扩展列导出 UTF-8 CSV。
- `StopForManualVerification`：检查点导出后保留页面并停止，等待人工处理。

在影刀中搭建：

1. 新建 PC 自动化应用并命名为 `TalonMart - Web Page to CSV`。
2. 添加上述五个子流程，按模板文档声明输入和输出参数。
3. Main 只实现批次初始化、输入循环、异常边界、结果追加和导出。
4. 为目标网站新增独立适配流程，并在 `InvokeSiteAdapter` 注册 `dataset_type` 路由。
5. 使用模板文档中的四场景矩阵完成人工验收，再接入真实站点。

影刀云端应用 ID、账号信息、捕获元素、页面截图和导出的应用包都可能包含环境信息，未完成脱敏审查前不得提交仓库。仓库中的 Markdown 契约是应用搭建和复核的版本化来源。

## 运行目录

| 目录 | 职责 |
| --- | --- |
| `var/rpa/inbox` | 接收影刀交付的不可变原始 CSV |
| `var/rpa/normalized` | 保存标准化 UTF-8 CSV |
| `var/rpa/archive` | 保存已完成批次的原始文件、输出和 manifest |
| `var/rpa/failed` | 隔离失败批次并提供重放输入 |

运行目录不得保存账号凭据，批次文件不得写入数据库。目录创建、原子写入、归档和重放属于后续 data-ops 任务。

## 扩展规则

新增站点或数据类型时：

1. 选择稳定且唯一的 `dataset_type`。
2. 保留七个通用输出列及其语义和顺序。
3. 只在站点实现中追加页面字段和站点错误代码。
4. 注册独立 `DatasetContract` 和 processor，不在通用读取核心中增加网站条件分支。
5. 提供合成或脱敏输入、原始 CSV fixture 和人工验收记录。
6. 明确登录、验证码、权限和访问限制的人工停止策略。
