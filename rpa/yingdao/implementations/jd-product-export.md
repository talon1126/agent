# 影刀京东商品采集适配器

本文定义 `dataset_type=jd_product` 的首个站点适配器。它接入通用
`TalonMart - Web Page to CSV` 应用的 `InvokeSiteAdapter` 扩展点，只读取商品页当前可见信息，
不调用京东内部接口，不自动执行登录，不处理订单，也不写数据库或修改 `items`。

## 输入与输出

输入 CSV 使用 `input_index,product_url`。适配器接收通用流程传入的 `dataset_type`、
`source_row`、`source_url`、`precheck_error` 和 `site_output_columns`，
返回 `adapter_result`。

站点字段顺序固定为：

```text
jd_sku_id,title,display_price,shop_name,primary_image_url,capture_region
```

通用字段仍由主流程生成：

```text
dataset_type,batch_id,input_index,source_url,captured_at,crawl_status,error_code
```

每个输入行必须产生恰好一条输出行。页面失败时不得用默认商品名、默认价格或默认店铺填充空值。

## 影刀流程结构

在影刀应用中建立 `JdProductAdapter.flow`，由 `InvokeSiteAdapter.flow` 调用。
子流程仅在 `dataset_type == "jd_product"` 时生成京东字段；其他数据集继续返回通用
`adapter_error` 和 `site_output_columns` 空值，不引入京东字段。第一版京东逻辑按以下顺序执行：

1. `ParseJdSkuId`
2. `GetOpenedJdPage`
3. `ExtractJdProduct`
4. `BuildJdProductRow`
5. `ClassifyJdCaptureResult`

### `ParseJdSkuId`

先移除查询参数和片段，再按协议、主机前缀和末级路径进行保守校验：

- 协议必须为 `https`。
- 主机必须为 `item.jd.com`；仓库测试数据允许保留域名 `item.jd.invalid`。
- 路径必须完整匹配 `/<数字>.html`，查询参数和片段不参与 SKU 解析。

解析成功返回数字字符串 `jd_sku_id`。任何条件不满足时直接构建
`crawl_status=failed,error_code=invalid_input`，不打开页面。

### `GetOpenedJdPage`

通用模板负责打开 `source_url` 和处理导航异常。适配器通过影刀的“获取已打开的网页对象”取得
当前商品页对象，再把该对象传给四个“获取元素信息(web)”节点。元素必须保存在影刀元素库中，
并优先使用稳定的 id、class、属性和局部相对路径，禁止依赖完整绝对 XPath。

浏览器导航失败由通用模板映射为 `failed/navigation_failed`，等待超时映射为
`failed/page_timeout`，登录或验证码映射为 `failed/manual_verification_required`，访问限制映射为
`failed/access_restricted`，并按需保存错误截图。第一版适配器不录制专用页面状态元素，也不尝试
绕过页面控制。

### `ClassifyJdCaptureResult`

适配器按四个采集结果映射状态：

| 采集结果 | `crawl_status` | `error_code` | 行为 |
| --- | --- | --- | --- |
| `precheck_error` 非空或 `dataset_type` 不匹配 | `failed` | `invalid_input` | 保留空站点字段 |
| 标题、价格、店铺和主图均非空 | `success` | 空字符串 | 返回采集结果 |
| 四个采集字段至少一个为空 | `partial` | `field_missing` | 保留已采集字段，不合成缺失值 |

遇到验证码、登录墙或访问限制时，由通用模板停止并保留当前页面；自动化不输入账号、密码、
Cookie 或 Token。

### `ExtractJdProduct`

只读取当前页面可见值，元素库逻辑名固定如下：

| 逻辑名 | 输出字段 | 采集规则 |
| --- | --- | --- |
| `JdProductTitle` | `title` | 商品标题可见文本，去除首尾空白 |
| `JdDisplayPrice` | `display_price` | 当前页面展示文本原样保留，不推算促销价 |
| `JdShopName` | `shop_name` | 商品主体关联店铺的可见名称 |
| `JdPrimaryImage` | `primary_image_url` | 主图 `src` 或 `data-origin` 的绝对 URL |

`capture_region` 继续保留在 CSV 契约中，但第一版固定写入空字符串，不录制页面地区元素。
标题、价格、店铺或主图缺失时保留空字符串，不合成值；四个采集字段均非空时为 `success`，
至少一个字段缺失时为 `partial/field_missing`。

### `BuildJdProductRow`

把 `jd_sku_id`、抽取值、页面状态和错误代码合并为字典后返回给
`AppendExportRow`。除 `invalid_input` 外，只要 URL 已成功解析，即使页面失效也保留
`jd_sku_id` 以便对账。失败时其余站点字段保持空字符串。

## 错误截图与安全

错误截图写入运行目录 `var/rpa/failed`，命名为
`{batch_id}_{input_index}_{error_code}.png`。截图不得提交到 Git，也不得包含账号、
密码、Cookie、Token、手机号或收货地址。人工验证状态必须停止批次并保留当前页面，
由操作员确认后重新运行，不尝试绕过站点控制。

## 脱敏验收

仓库中的 `fixtures/rpa/jd_product_urls.csv` 只使用 `.invalid` 域名，用于核对
`ParseJdSkuId`、状态映射和一行输入对应一行输出的合同，不会触发真实网络访问。
`fixtures/rpa/jd_product_export.csv` 固定覆盖：

1. 正常商品：`success`。
2. 第三方商品缺少展示价格：`partial/field_missing`。
3. 商品下架或页面失效：`failed/navigation_failed`。
4. 非法商品 URL：`failed/invalid_input`。

J3 真实页面验收使用一个已登录的正常商品页核对四个元素抽取、运行日志和错误列表。输入清单、
输出行数、页面状态、错误代码和错误截图的端到端验收属于 J7。真实 URL、页面截图、Cookie 和
账号信息均不得写入仓库。

## 当前实施状态

截至 2026-07-18，影刀应用中已经建立 `JdProductAdapter.flow`，并从
`InvokeSiteAdapter.flow` 接入。调用参数为 `dataset_type`、`source_row`、
`source_url`、`precheck_error` 和 `site_output_columns`；返回包装值通过
`adapter_process_result.adapter_result` 写回 `adapter_result`。设计器错误列表为
“暂无错误数据”。

当前流程已接入 `JdProductTitle`、`JdDisplayPrice`、`JdShopName` 和
`JdPrimaryImage` 四个元素：前三个读取文本，主图读取 `src` 属性。采集值写回
`site_fields`，随后覆盖 `crawl_status` 和 `error_code`；`capture_region` 固定留空。

2026-07-18 已在登录后的影刀浏览器中打开京东商品页，保存流程并完成设计器运行验收；运行日志显示
“开始执行”和“执行结束”，错误列表为“暂无错误数据”。J3 适配器实现完成。输入清单循环、原始 CSV
导出和 pandas 处理的完整端到端验收属于 J7。
