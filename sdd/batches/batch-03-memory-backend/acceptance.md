# batch-03 — MemoryBackend 默认实现 验收标准

> 对照 [design.md](design.md) 的验收清单。所有标准必须在 batch-03 完成时通过。

---

## 一、功能验收

### AC-MEM-01：`read()` — 按 key 读取记忆

- [ ] **AC-MEM-01.1**：对已存在的 key 调用 `read(key, namespace)`，返回与 `write()` 时相同的 value（字符串形式）
- [ ] **AC-MEM-01.2**：对不存在的 key 调用 `read(key, namespace)`，返回 `None`
- [ ] **AC-MEM-01.3**：对不存在的 namespace 调用 `read(key, namespace)`，返回 `None`
- [ ] **AC-MEM-01.4**：同一 namespace 下存在多个 key 时，`read()` 仅返回指定 key 的值

### AC-MEM-02：`write()` — 写入记忆

- [ ] **AC-MEM-02.1**：调用 `write(key, value, namespace)` 后，对应的 `.md` 文件被创建在正确的子目录下
- [ ] **AC-MEM-02.2**：写入的 `.md` 文件包含正确的 YAML frontmatter（key, namespace, timestamp, metadata）
- [ ] **AC-MEM-02.2a**：`timestamp` 是一个有效的 Unix epoch 浮点数（如 `1717430400.0`）
- [ ] **AC-MEM-02.3**：写入的 `.md` 文件正文包含 value 的字符串表示
- [ ] **AC-MEM-02.4**：对同一 key 重复 `write()`，后一次覆盖前一次（`.md` 文件被更新）
- [ ] **AC-MEM-02.5**：`write()` 后立即在同一实例上 `read()` 能读到刚写入的值
- [ ] **AC-MEM-02.6**：写入非字符串 value（int, list, dict 等）时，`str(value)` 转换后正确写入
- [ ] **AC-MEM-02.7**：写入空字符串 value（`""`），`read()` 返回 `""`
- [ ] **AC-MEM-02.8**：写入含特殊字符（`\n`, `---`, 中文, emoji）的 value，正确保存和读取

### AC-MEM-03：`search()` — 搜索记忆

- [ ] **AC-MEM-03.1**：`search(query, namespace, limit=10)` 返回包含 query 子串（大小写不敏感）的记忆项列表
- [ ] **AC-MEM-03.2**：匹配范围包括 key 和 value（两者任一匹配即返回）
- [ ] **AC-MEM-03.3**：返回结果按时间戳降序排序（最新写入的在前）
- [ ] **AC-MEM-03.4**：返回结果数量不超过 `limit` 参数
- [ ] **AC-MEM-03.5**：query 匹配不到任何记忆时返回空列表 `[]`
- [ ] **AC-MEM-03.6**：对不存在的 namespace 调用 `search()`，返回空列表 `[]`
- [ ] **AC-MEM-03.7**：空字符串 query（`""`）时返回空列表 `[]`
- [ ] **AC-MEM-03.8**：`limit=0` 时返回空列表 `[]`
- [ ] **AC-MEM-03.9**：`limit<0` 时返回全部匹配结果（不截断）
- [ ] **AC-MEM-03.10**：search 过程中不读磁盘（仅查询内存索引）

### AC-MEM-04：`list_namespaces()` — 列出命名空间

- [ ] **AC-MEM-04.1**：至少写入过一个记忆后，`list_namespaces()` 返回包含该 namespace 的列表
- [ ] **AC-MEM-04.2**：没有任何记忆时，返回空列表 `[]`
- [ ] **AC-MEM-04.3**：多次写入不同 namespace 后，返回所有已使用的 namespace 列表（去重）

---

## 二、持久化验收

### AC-MEM-05：跨实例持久化

- [ ] **AC-MEM-05.1**：创建 `MdMemory` 实例 A，写入一条记忆 → 销毁实例 A
- [ ] **AC-MEM-05.2**：创建新的 `MdMemory` 实例 B（指向同一目录），`read()` 能读到 A 写入的记忆
- [ ] **AC-MEM-05.3**：实例 B 的 `search()` 能找到 A 写入的记忆
- [ ] **AC-MEM-05.4**：实例 B 的 `list_namespaces()` 包含 A 使用的 namespace

---

## 三、边界条件验收

### AC-MEM-06：目录管理

- [ ] **AC-MEM-06.1**：传入不存在的路径，`__init__` 自动创建目录（不抛异常）
- [ ] **AC-MEM-06.2**：传入已存在且有旧记忆的目录，启动时正确加载所有已有记忆
- [ ] **AC-MEM-06.3**：传入空目录，启动正常，`list_namespaces()` 返回 `[]`
- [ ] **AC-MEM-06.4**：路径中包含 `~`（用户主目录），正确展开

### AC-MEM-07：容错

- [ ] **AC-MEM-07.1**：目录中存在非 `.md` 文件（如 `.DS_Store`, `README.txt`），启动时忽略不报错
- [ ] **AC-MEM-07.2**：单个 `.md` 文件 frontmatter 格式错误，启动时记录 WARNING 并跳过该文件（不阻塞整体启动）
- [ ] **AC-MEM-07.3**：`.md` 文件没有 frontmatter（无 `---` 分隔符），跳过并记录 WARNING
- [ ] **AC-MEM-07.4**：`MEMORY.md` 被手动删除后，不影响正常读写（下次 `write()` 时重建）

### AC-MEM-08：大规模数据性能

- [ ] **AC-MEM-08.1**：1000 条记忆的目录，启动扫描时间 < 1 秒
- [ ] **AC-MEM-08.2**：1000 条记忆中 `search()` 响应时间 < 10ms
- [ ] **AC-MEM-08.3**：`read()` 响应时间 < 1ms（从内存索引读取）

---

## 四、接口符合性验收

### AC-MEM-09：Protocol 符合性

- [ ] **AC-MEM-09.1**：`MdMemory` 实例可通过 `isinstance(m, MemoryBackend)` 检查（`@runtime_checkable`）
- [ ] **AC-MEM-09.2**：`MdMemory` 可成功注册到 DI 容器：`container.register(MemoryBackend, MdMemory(...))`
- [ ] **AC-MEM-09.3**：编排器使用 `MdMemory` 作为 `MemoryBackend` 时，Phase 1 的 `search()` 正常执行
- [ ] **AC-MEM-09.4**：`write()` / `read()` / `search()` / `list_namespaces()` 方法签名与 `MemoryBackend` Protocol 完全一致

### AC-MEM-10：类型正确性

- [ ] **AC-MEM-10.1**：`search()` 返回的列表元素为 `MemoryItem` 实例
- [ ] **AC-MEM-10.2**：`MemoryItem` 的 5 个字段（key, value, namespace, timestamp, metadata）全部填充正确
- [ ] **AC-MEM-10.3**：`read()` 返回值类型为 `Optional[Any]`
- [ ] **AC-MEM-10.4**：`list_namespaces()` 返回值为 `List[str]`

---

## 五、不验证的内容

以下内容明确不在 batch-03 验收范围内：

- ❌ 多线程并发安全（batch-03 单线程）
- ❌ 向量搜索 / 语义搜索（未来高级实现）
- ❌ MemoRy.md 索引文件的内容正确性（它是辅助性的，启动不依赖它）
- ❌ 与 Sensor 的集成测试（Sensor 在 batch-07）
- ❌ 与 ContextAssembler 的集成测试（ContextAssembler 在 batch-05）
