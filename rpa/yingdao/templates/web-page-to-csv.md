# 影刀通用网页导出 CSV 模板

本文定义影刀应用 `TalonMart - Web Page to CSV` 的可复用流程骨架。应用只负责输入循环、公共状态、站点适配调用、结果累积和原始 CSV 导出。页面打开、就绪判断和业务字段提取必须由独立站点适配流程完成。

模板不包含任何生产站点的页面元素、CSS/XPath、字段名或页面状态规则。所有运行页面必须已经获得访问授权。

## 应用结构

在影刀中新建 PC 自动化应用，并按下列顺序建立主流程和子流程：

1. 主流程依次调用 `LoadInputRows`、`InvokeSiteAdapter`、`AppendExportRow` 和 `ExportRawCsv`。
2. `StopForManualVerification` 只在当前结果已追加且检查点 CSV 已导出后调用。
3. 新站点只增加适配流程，并在 `InvokeSiteAdapter` 中注册路由；不得复制主循环和导出逻辑。

```text
Main
  -> LoadInputRows
  -> For each input row
       -> InvokeSiteAdapter
       -> AppendExportRow (finally, exactly once)
       -> manual verification?
            -> ExportRawCsv (checkpoint)
            -> StopForManualVerification
  -> ExportRawCsv (normal completion)
```

## 应用输入

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `dataset_type` | string | 是 | 小写 snake_case 的适配器路由键 |
| `input_csv_path` | string | 是 | UTF-8 输入 CSV 的绝对路径 |
| `output_directory` | string | 是 | 原始 CSV 输出目录 |
| `source_url_column` | string | 是 | 输入行中来源 URL 所在列名 |
| `site_output_columns` | list[string] | 是 | 站点扩展列的固定顺序 |

启动时必须验证：

- `dataset_type`、`input_csv_path`、`output_directory` 和 `source_url_column` 非空。
- `dataset_type` 只包含小写字母、数字和下划线，且以字母开头。
- `site_output_columns` 不得包含任何通用列。
- 输入文件存在且包含 `source_url_column`；读取失败时应用整体失败，不生成伪造结果。

## 通用输出列

原始 CSV 的固定列头为：

```text
dataset_type,batch_id,input_index,source_url,captured_at,crawl_status,error_code
```

`site_output_columns` 只能追加在固定列之后，不能改变通用列的名称、顺序或语义。

| 列 | 生成位置 | 规则 |
| --- | --- | --- |
| `dataset_type` | Main | 等于应用输入，不允许适配器覆盖 |
| `batch_id` | Main | 每次运行只生成一次 |
| `input_index` | `LoadInputRows` | 正整数且在批次内唯一 |
| `source_url` | Main | 从当前输入行的 `source_url_column` 复制 |
| `captured_at` | Main | 当前行完成页面判断时的 ISO 8601 UTC 时间 |
| `crawl_status` | 适配器结果 | 只能是 `success`、`partial` 或 `failed` |
| `error_code` | 适配器结果或 Main | `success` 时为空，其他状态必须非空 |

## 主流程变量

| 变量 | 初始值 | 生命周期 |
| --- | --- | --- |
| `batch_id` | 一次性 UUID 或等价随机标识 | 整个运行批次 |
| `batch_captured_at` | UTC 时间 `yyyyMMddTHHmmssZ` | 用于安全文件名 |
| `input_rows` | `[]` | `LoadInputRows` 输出 |
| `export_rows` | `[]` 或空数据表格 | 所有已完成输入行 |
| `current_result` | 空字典 | 每轮循环重建 |
| `row_appended` | `False` | 防止一行重复追加 |
| `manual_stop_requested` | `False` | 控制检查点导出和人工停止 |

## 影刀可视化配置约束

影刀的字符串输入框和 Python 表达式输入框外观相近，搭建时必须逐项确认“Python 模式”已经开启。变量名或表达式如果留在文本模式，会被当作普通字符串，典型表现包括循环元素变成字符串、`export_rows` 不是列表，以及文件路径包含多余引号。

- Main 的 `batch_id`、`batch_captured_at` 和 `export_rows` 使用 Python 模式初始化。
- 两个 `ForEach列表循环` 的列表输入必须使用 Python 模式，不能保留空输入。
- `设置键值对` 的目标字典使用 Python 变量，键名使用普通文本，动态值使用 Python 表达式。
- `调用流程` 的变量参数必须在切换到 Python 模式后，再写入 `PART_SyntaxEditor`。
- `InvokeSiteAdapter` 的调用结果保存为 `adapter_process_result`，传给 `AppendExportRow` 时使用 `adapter_process_result.adapter_result` 取得子流程输出字典。

当前 PC 可视化模板在 Main 的循环输入处直接用 `csv.DictReader` 生成标准化字典行，并补齐 `source_row`、`input_index`、`source_url` 和 `precheck_error`。这是为了避免把“读取 CSV 数据”产生的二维列表误传给主循环；`LoadInputRows` 仍保留同样的校验和标准化职责，后续升级影刀版本时可在确认流程输出解包行为后收敛为单一实现。

## `LoadInputRows`

输入：`input_csv_path`、`source_url_column`。

输出：`input_rows`，每个对象至少包含 `input_index`、`source_url`、`source_row` 和可选 `precheck_error`。

流程：

1. 以 UTF-8 读取 CSV，保留原始字符串值，不自动把长编号转为科学计数法。
2. 如果存在 `input_index` 列，接受正整数且批次内唯一的值。
3. 如果 `input_index` 缺失、为空、重复或不是正整数，使用文件中的一基行号作为稳定回退值，并设置 `precheck_error=invalid_input`。
4. 从 `source_url_column` 复制 `source_url`；空值同样设置 `precheck_error=invalid_input`。
5. 不删除错误输入行。`LoadInputRows` 返回的行数必须等于输入 CSV 数据行数。

## `InvokeSiteAdapter`

输入：`dataset_type`、`source_row`、`source_url`。

输出：

```text
{
  crawl_status: success | partial | failed,
  error_code: string,
  site_fields: object,
  requires_manual_verification: bool,
  verification_reason: string
}
```

路由规则：

1. 根据 `dataset_type` 调用已注册站点适配流程。
2. 未注册类型返回 `failed + adapter_error`，不得终止整个批次。
3. `precheck_error` 存在时不打开页面，直接返回 `failed + invalid_input`。
4. 校验适配结果：状态值合法、失败代码非空、`site_fields` 只能包含 `site_output_columns`。
5. 适配器抛出普通异常时捕获为 `failed + adapter_error`，只记录异常类别，不把页面内容或凭据写入日志。
6. 适配器要求人工验证时返回 `failed + manual_verification_required`，由 Main 在追加结果后处理停止。

站点适配器可以设置通用状态值，但不得返回或覆盖 `dataset_type`、`batch_id`、`input_index`、`source_url` 和 `captured_at`。

## `AppendExportRow`

输入：当前公共字段、适配结果、`site_output_columns` 和 `export_rows`。

流程：

1. 按通用列顺序构造基础行。
2. 按 `site_output_columns` 顺序追加字段；缺失字段写空字符串，并把 `success` 降级为 `partial + field_missing`。
3. 未声明的站点字段拒绝写入，并把当前行改为 `failed + adapter_error`。
4. 把完整行追加到 `export_rows`，随后设置 `row_appended=True`。
5. Main 在每轮 `finally` 中检查 `row_appended`。即使适配器异常，也必须调用一次 `AppendExportRow`。

核心不变量：每个输入行恰好一条结果行；`len(export_rows)` 始终等于已经离开循环的输入行数。

## `ExportRawCsv`

输入：`dataset_type`、`batch_id`、`batch_captured_at`、`output_directory`、`site_output_columns` 和 `export_rows`。

输出文件名固定为：

```text
{dataset_type}_{batch_id}_{captured_at}.csv
```

其中 `{captured_at}` 使用 `batch_captured_at` 的 `yyyyMMddTHHmmssZ` 值，文件名不得包含冒号或路径分隔符。

影刀配置要求：

1. 列顺序为七个通用列加 `site_output_columns`。
2. 使用 Python 模式把字典列表转换成“表头行 + 数据行”的二维列表；不能把 `export_rows` 字典列表直接交给“数据写入 CSV”，否则会写成一行字典字符串。
3. 使用“数据写入 CSV”或等价指令导出列头和数据。
4. 文件必须使用 UTF-8 编码；再次读取表头和行数完成导出后校验。
5. 行数或表头校验失败时返回导出错误，不覆盖已经存在的同名文件。

基础实现的二维列表表达式为：

```python
([list(export_rows[0].keys())] + [list(row.values()) for row in export_rows]) if export_rows else []
```

## `StopForManualVerification`

输入：`batch_id`、`input_index`、`verification_reason` 和已导出的检查点文件路径。

执行顺序：

1. 确认当前行已经由 `AppendExportRow` 追加。
2. 确认 `ExportRawCsv` 已生成包含当前行的检查点 CSV。
3. 保留当前页面和浏览器窗口，不关闭标签页，不继续点击或提交。
4. 显示本地人工处理对话框，只展示 `batch_id`、`input_index`、稳定错误代码和检查点路径。
5. 停止后续输入循环，由操作员处理页面并决定是否重新运行剩余输入。

凭据、Cookie、Token 和验证码内容不得写入 CSV、日志、变量持久化或仓库。日志只允许记录 `batch_id`、`input_index`、`crawl_status` 和 `error_code`；`source_url` 查询参数也不得直接打印。

## Main 异常结构

```text
for current_input in input_rows:
    row_appended = False
    current_result = default failed row
    try:
        adapter_result = InvokeSiteAdapter(...)
        current_result = merge common fields and adapter result
    catch ordinary adapter exception:
        current_result = failed + adapter_error
    finally:
        if not row_appended:
            AppendExportRow(current_result)

    if current_result.requires_manual_verification:
        checkpoint = ExportRawCsv(export_rows)
        StopForManualVerification(checkpoint)
        stop application

ExportRawCsv(export_rows)
```

禁止在 `try` 和 `catch` 分支分别追加结果；所有路径统一在 `finally` 中追加，避免重复行和丢失行。

## 人工验收矩阵

验收使用本地 `data:` 页面或经过脱敏的测试页面，并建立临时 `acceptance_stub` 适配流程。该流程只用于验证模板状态机，不进入生产站点实现目录。

| 场景 | 适配器行为 | 预期结果 |
| --- | --- | --- |
| 成功场景 | 返回完整 `site_fields` | 一行 `success`，`error_code` 为空 |
| 字段缺失场景 | 少返回一个声明字段 | 一行 `partial + field_missing` |
| 适配失败场景 | 主动抛出测试异常 | 一行 `failed + adapter_error`，后续输入继续 |
| 人工验证场景 | 返回 `requires_manual_verification=True` | 一行 `failed + manual_verification_required`，检查点 CSV 导出后保留页面并停止 |

验收检查：

- 输入四行时，适配失败之前和之后的结果均存在；人工停止时检查点包含已处理行。
- 重新移除人工验证场景后完整运行，输入行数与输出行数相等。
- 文件名包含 `dataset_type`、`batch_id` 和采集时间。
- 表头顺序固定，站点字段只追加在通用列之后。
- 日志和 CSV 不包含账号、密码、Cookie、Token 或验证码内容。
- 模板没有任何生产站点字段、选择器或页面状态判断。

2026-07-18 使用三行脱敏 URL 和未注册的 `acceptance_stub` 适配器完成基础烟测：输出三行、七个通用列、单一 `batch_id`，每行保留 `source_url`，并统一得到 `failed + adapter_error`。该烟测只验证通用循环、结果追加、失败兜底和 CSV 落盘；成功、字段缺失和人工验证场景仍按上表进行站点适配器人工验收。
