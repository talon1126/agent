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

`JD_PRODUCT_DATASET_CONTRACT` 要求原始 CSV 完整声明通用列和京东站点列，并使用 `batch_id + input_index` 作为输入行唯一键。字段存在不代表字段值一定非空；第一版 `capture_region` 固定为空，`jd_product` processor 会把通过校验的行写入标准化 CSV，并把 `partial`、`failed`、重复或字段异常行保留在失败 CSV。

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

运行目录不得保存账号凭据，批次文件不得写入数据库。

## 京东端到端文件链路

影刀完整导出时把 `output_directory` 指向 `<repo>/var/rpa/inbox`，并记录
`ExportRawCsv` 返回的 `input_path`、`batch_id`、输入行数和导出行数。已位于目标 inbox 的
文件可以直接交给脚本；fixture 或其他外部文件会先复制到 `OutputRoot/inbox`，避免生命周期
归档移动原文件：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_data_ops.ps1 `
  -InputPath fixtures\rpa\jd_product_export.csv `
  -DatasetType jd_product `
  -OutputRoot D:\tmp\talonmart-rpa-acceptance
```

成功后检查：

- `normalized/` 中同时存在标准化 CSV 和保留 partial/failed 行的失败 CSV。
- `archive/jd_product/{batch_id}/` 中存在 `source.csv`、两份输出和 `manifest.json`。
- manifest 的 `input_rows` 等于影刀原始 CSV 行数，且等于 normalized 与 failed 行数之和。
- 仓库 fixture 或其他外部 `InputPath` 仍存在且字节未改变。

pandas 批次失败时，原始 source 会进入 `failed/jd_product/{batch_id}`。修正数据时创建独立
副本，并调用 `replay_batch(manifest_path, output_root=..., source_path=corrected_copy)`；
重放会验证 dataset contract 和 batch_id，不修改失败归档中的 source，也不重新访问网页。

## 京东 URL 发现与自动编排

J9 使用 `scripts/run_jd_product_pipeline.ps1` 串联真实京东 URL 发现、影刀采集和 pandas
处理。URL discovery 由 Playwright 打开调用者指定的京东关键词、搜索页或分类页，在
`MaxPages` 和 `MaxItems` 边界内处理懒加载与分页，从商品详情链接或搜索卡片客服链接的
`pid` 提取数字 SKU，并统一输出：

```text
input_index,product_url
1,https://item.jd.com/{sku}.html
```

自动测试直接访问真实京东，不读取合成列表页 HTML，也不使用网络 mock。网络不可达、京东页面
结构变化、登录失效或跳转验证页会明确失败；流程不尝试绕过验证码或访问限制。
需要复用已授权的京东登录态时，将本机 Playwright 状态文件路径放入
`JD_PLAYWRIGHT_STORAGE_STATE`。该文件包含登录状态，只能保存在仓库外的受控临时目录，
不得提交、写入日志或复制到批次产物；未配置时仍会真实访问京东，并在登录或验证受限时明确失败。

影刀主流程必须从应用入参或“获取应用参数”读取三个字符串，并覆盖 J2/J7 中的本地默认值：

| 参数 | 用途 |
| --- | --- |
| `batch_id` | 当前流水线批次 ID，原样写入每条原始 CSV 行 |
| `input_csv` | J9 discovery 生成的绝对输入路径 |
| `raw_output_csv` | 影刀必须写出的原始 CSV 绝对路径 |

能够导出 `.shr` 的影刀环境可以使用本地命令方式，脚本通过
`ShadowBot.exe --mode=robot --app-file=... --app-params=...` 启动；企业版可以使用 OpenAPI，
凭据只从 `YINGDAO_ACCESS_KEY_ID` 和 `YINGDAO_ACCESS_KEY_SECRET` 环境变量读取。当前客户端或
账号没有 `.shr`/OpenAPI 能力时，在设计器中手工运行主流程并生成约定的 discovery CSV 与
`raw_output_csv`，随后使用同一 `batch_id` 重新执行脚本。脚本检测到两个交接文件后会直接进入
pandas 阶段，不再要求 runner、应用包或企业凭据。所有模式都不得把凭据写入 CSV、manifest、
命令日志或仓库。

个人版示例：

```powershell
$env:YINGDAO_APP_FILE = "D:\rpa\TalonMart-Web-Page-to-CSV.shr"
powershell -ExecutionPolicy Bypass -File scripts\run_jd_product_pipeline.ps1 `
  -Keyword "手机" `
  -BatchId jd_phone_001 `
  -OutputRoot D:\tmp\talonmart-jd-pipeline `
  -MaxPages 1 `
  -MaxItems 20 `
  -YingdaoMode command
```

企业版在环境变量中配置 access key/secret，并传入 `YingdaoAccountName` 和
`YingdaoRobotUuid`。最终结果保存在 `results/{batch_id}/pipeline_result.json`，退出码固定为：

| 退出码 | 状态 |
| ---: | --- |
| `0` | 全部成功 |
| `10` | 有有效标准化结果，同时存在失败商品 |
| `20` | URL 发现失败 |
| `30` | 影刀启动、采集、人工验证或输出失败 |
| `40` | pandas 处理失败 |

discovery CSV 或影刀原始 CSV 已存在时，相同 batch 可以从后续阶段继续；批次锁保证同一
batch 不会并发启动两个影刀实例。全链路只生成文件，不新增数据库表，也不查询或修改 `items`。

手工影刀交接完成后的续跑命令不需要设置 `YINGDAO_APP_FILE`：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_jd_product_pipeline.ps1 `
  -SeedUrl "https://search.jd.com/Search?keyword=手机" `
  -BatchId jd_phone_001 `
  -OutputRoot D:\tmp\talonmart-jd-pipeline `
  -MaxPages 1 `
  -MaxItems 20 `
  -YingdaoMode command
```

## 新增站点实现检查清单

1. 选择稳定且唯一的 `dataset_type`，并在 `implementations/` 下新增一份站点适配说明。
2. 提供只含合成或脱敏数据的输入 fixture，明确 URL 列、`input_index` 和预期行数。
3. 列出页面状态和停止策略，至少覆盖正常、字段缺失、导航失败、访问限制和人工验证。
4. 固定 `site_output_columns` 的字段映射、顺序、来源元素以及空值语义。
5. 声明站点错误代码；通用错误代码保持原语义，不把异常页面内容写入错误信息。
6. 在 `InvokeSiteAdapter` 注册路由，不复制 Main、输入循环、`AppendExportRow` 或导出骨架。
7. 完成人工验收矩阵，保存 batch_id、输入/输出行数和稳定状态，不保存凭据或真实会话。

## 新增 pandas processor 检查清单

1. 在 `processors/` 下建立具体 `DatasetContract`，站点字段不得放入通用 core。
2. 实现 `validate`、`normalize` 和 `split_results`，并通过 `register_processor` 注册。
3. 固定标准化 CSV 和失败 CSV 的列顺序、文件名以及 `batch_id + input_index` 对账规则。
4. 失败 CSV 必须保留 `input_index`、`source_url`、`crawl_status` 和 `error_code`。
5. 为缺通用列、未知 dataset_type、站点字段缺失、类型解析失败、重复数据、空文件和失败重放编写测试。
6. 确认 processor 不导入数据库驱动、不查询或修改 `items`，也不调用业务 API 或下游系统。
7. 使用 `scripts/run_data_ops.ps1` 验证 inbox、normalized、failed、archive 和 manifest 全链路。

## 京东人工验收清单

| 场景 | 操作 | 预期结果 |
| --- | --- | --- |
| 正常商品 | 在已登录浏览器打开字段完整的商品页 | `success`，SKU、标题、展示价格、店铺和主图非空 |
| 第三方店铺 | 打开可访问的第三方店铺商品页 | 字段完整时 `success`；缺字段时 `partial/field_missing` |
| 无货或下架 | 打开无货、已下架或失效商品页 | 可访问但字段缺失时 `partial/field_missing`；导航失败时 `failed/navigation_failed` |
| 无效 URL | 输入非商品页或无法解析 SKU 的 URL | 不打开页面，返回 `failed/invalid_input` |
| 登录或验证中断 | 使用需要登录、验证码或人工确认的页面 | 先导出检查点，再返回 `failed/manual_verification_required` 并停止 |

每次人工验收都要确认输入行数等于导出行数，日志不包含 Cookie、Token、账号或验证码内容。
