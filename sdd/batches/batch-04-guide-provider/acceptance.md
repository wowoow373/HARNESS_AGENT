# batch-04 — GuideProvider 默认实现 验收标准

> 对照 [design.md](design.md) 的验收清单。所有标准必须在 batch-04 完成时通过。

---

## 一、功能验收

### AC-GP-01：`get_guides()` — 从 Markdown 文件正确解析所有 5 个字段

- [ ] **AC-GP-01.1**：解析含 H1 标题的文件，`identity` 字段包含 H1 标题下的文本内容
- [ ] **AC-GP-01.2**：解析含 `## 能力` section 的文件，`capabilities` 列表包含正确的列表项
- [ ] **AC-GP-01.3**：解析含 `## 规则` section 的文件，`rules` 列表包含正确的列表项
- [ ] **AC-GP-01.4**：解析含 `## 约束` section 的文件，`constraints` 列表包含正确的列表项
- [ ] **AC-GP-01.5**：解析含 `## 示例` section 的文件，`examples` 列表包含 `Example` 实例，且 `input`/`output` 字段正确
- [ ] **AC-GP-01.6**：解析仅含 H1 标题（最低限度文件）的文件，返回 identity 非空、其他字段为空的 GuidesBundle

### AC-GP-02：标题关键词匹配（中英文）

- [ ] **AC-GP-02.1**：使用中文标题（`## 能力`、`## 规则`、`## 约束`、`## 示例`）正确识别所有 section
- [ ] **AC-GP-02.2**：使用英文标题（`## Capabilities`、`## Rules`、`## Constraints`、`## Examples`）正确识别所有 section
- [ ] **AC-GP-02.3**：中英文混合标题（如 `## 行为规则与Behavior Rules`）正确匹配
- [ ] **AC-GP-02.4**：使用变体关键词标题（`## 行为准则`→rules, `## 技能`→capabilities, `## 限制`→constraints, `## 范例`→examples）正确匹配

---

## 二、容错验收

### AC-GP-03：文件缺失处理

- [ ] **AC-GP-03.1**：传入不存在的文件路径，`get_guides()` 不抛异常，返回空的 GuidesBundle
- [ ] **AC-GP-03.2**：传入多个路径，其中全部文件都不存在时，返回空的 GuidesBundle（不抛异常）
- [ ] **AC-GP-03.3**：文件路径含 `~` 展开，正确处理

### AC-GP-04：边界输入处理

- [ ] **AC-GP-04.1**：传入空文件（0 字节），`get_guides()` 返回空 GuidesBundle（不抛异常）
- [ ] **AC-GP-04.2**：文件不含任何 H1 标题（如纯规则列表），`identity` 为空字符串，其他 section 正常解析
- [ ] **AC-GP-04.3**：文件含无法匹配任何已知字段的 H2 标题，该 section 内容被忽略（不报错，不混入其他字段）
- [ ] **AC-GP-04.4**：examples section 下无 H3 子标题时，不生成 Example（或给出合理降级行为）
- [ ] **AC-GP-04.5**：`paths` 参数为空列表 `[]` 时，不抛异常，返回空 GuidesBundle

---

## 三、多文件聚合验收

### AC-GP-05：多文件合并

- [ ] **AC-GP-05.1**：两个文件各含 H1 标题，`identity` 正确拼接（用换行分隔）
- [ ] **AC-GP-05.2**：两个文件各含 rules，`rules` 正确追加合并（后一个文件的 rules 追加到末尾）
- [ ] **AC-GP-05.3**：两个文件含重复 capabilities，`capabilities` 去重（仅保留首次出现的项）
- [ ] **AC-GP-05.4**：部分文件缺失时不阻塞：缺失文件记录 WARNING，存在文件正常解析并返回合并结果
- [ ] **AC-GP-05.5**：单字符串路径（非列表）也能正常工作

---

## 四、内容解析准确性

### AC-GP-06：解析精度

- [ ] **AC-GP-06.1**：列表项以 `- ` 和 `* ` 开头均被正确识别
- [ ] **AC-GP-06.2**：文件含特殊字符（emoji、中文标点、代码块标记等），解析不崩溃，内容被正确保留
- [ ] **AC-GP-06.3**：identity 含多个段落（空行分隔），保留段落结构
- [ ] **AC-GP-06.4**：示例的 `**输入**:` / `**输出**:` 标记正确配对（一个输入对应紧邻的输出）

### AC-GP-07：示例解析

- [ ] **AC-GP-07.1**：使用中文标记 `**输入**:` / `**输出**:` 正确解析
- [ ] **AC-GP-07.2**：使用英文标记 `**Input**:` / `**Output**:` 正确解析
- [ ] **AC-GP-07.3**：使用无加粗标记 `输入:` / `输出:` 正确解析
- [ ] **AC-GP-07.4**：多个示例（含 `###` 子标题）各自独立解析，不混淆

---

## 五、接口符合性验收

### AC-GP-08：Protocol 符合性

- [ ] **AC-GP-08.1**：`FileGuideProvider` 实例可通过 `isinstance(g, GuideProvider)` 检查（`@runtime_checkable`）
- [ ] **AC-GP-08.2**：`FileGuideProvider` 可成功注册到 DI 容器：`container.register(GuideProvider, FileGuideProvider(...))`
- [ ] **AC-GP-08.3**：`get_guides()` 方法签名与 `GuideProvider` Protocol 一致（接收 `GuideContext`，返回 `GuidesBundle`）

### AC-GP-09：类型正确性

- [ ] **AC-GP-09.1**：`get_guides()` 返回值类型为 `GuidesBundle`
- [ ] **AC-GP-09.2**：`GuidesBundle.examples` 列表中的元素均为 `Example` 实例
- [ ] **AC-GP-09.3**：`GuidesBundle.capabilities`、`rules`、`constraints` 均为 `List[str]`
- [ ] **AC-GP-09.4**：`GuidesBundle.identity` 为 `str`

---

## 六、回归验收

### AC-GP-10：已有测试无回归

- [ ] **AC-GP-10.1**：运行 `pytest tests/ --ignore=tests/test_real_llm_trace.py -v`，所有已有测试通过
- [ ] **AC-GP-10.2**：`harness/interfaces/guide_provider.py` 未被修改
- [ ] **AC-GP-10.3**：`harness/interfaces/types.py` 未被修改

---

## 七、不验证的内容

以下内容明确不在 batch-04 验收范围内：

- ❌ 不验证网络/远程 GuideProvider 实现（那是未来的高级实现）
- ❌ 不验证 GuideProvider 在编排器中的实际调用（编排器已有调用逻辑，batch-04 只提供实现）
- ❌ 不验证与 ContextAssembler 的集成（ContextAssembler 在 batch-05）
- ❌ 不验证 YAML frontmatter 解析（batch-04 只解析 Markdown 标题层级）
- ❌ 不验证 TOML 格式的 guide 文件
- ❌ 不验证文件热更新触发机制（每次 `get_guides()` 调用时重新读取即为"热更新"，不额外实现 fs watcher）
- ❌ 不验证文件编码自动检测（仅支持 UTF-8）
- ❌ 不验证大文件性能（guide 文件通常 < 50KB）
