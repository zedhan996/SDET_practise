# Linux/线上定位命令与判断清单

## 1. 先建立定位顺序

```text
现象
→ 客户端是否能连接
→ 端口是否监听
→ 进程是否存在
→ 服务日志和 request id
→ CPU/内存/磁盘是否异常
→ 最小复现与结论
```

不要一看到 500 就直接改代码。先确认请求是否到达、由哪个进程处理、在哪一层失败。

## 2. 服务未启动

### Linux

```bash
curl -i http://127.0.0.1:8000/
ps -ef | grep '[u]vicorn'
ss -lntp | grep ':8000'
systemctl status catalog.service
journalctl -u catalog.service -n 100 --no-pager
```

### Windows PowerShell

```powershell
curl.exe -i http://127.0.0.1:8000/
Get-Process python -ErrorAction SilentlyContinue
Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
```

### 判断

```text
curl connection refused
且没有 8000 监听
→ 服务未启动、启动失败或监听地址/端口错误
```

如果进程存在但没有端口监听，继续查启动日志，而不是只看进程名。

## 3. 端口占用

### Linux

```bash
ss -lntp | grep ':8000'
lsof -nP -iTCP:8000 -sTCP:LISTEN
ps -fp <PID>
```

### Windows PowerShell

```powershell
Get-NetTCPConnection -LocalPort 8000 -State Listen |
    Select-Object LocalAddress,LocalPort,OwningProcess
Get-Process -Id <PID>
```

### 判断

```text
8000 已被其他进程监听
→ 新服务无法绑定该端口
→ 先确认占用进程和用途，再选择换端口或停止正确进程
```

不要根据 PID 直接执行 `kill -9` 或强制停止。先用 `ps -fp`/`Get-Process` 确认进程归属。

## 4. HTTP 500

### 第一步：保存响应和 request id

```bash
curl -i -H 'X-Request-ID: triage-500-001' \\
  http://127.0.0.1:8000/items/999999
```

```powershell
curl.exe -i -H "X-Request-ID: triage-500-001" `
    http://127.0.0.1:8000/items/999999
```

### 第二步：按 request id 查日志

```bash
grep 'triage-500-001' app.log
journalctl -u catalog.service --since '10 minutes ago' | grep 'triage-500-001'
```

```powershell
Select-String -Path .\server.log -Pattern 'triage-500-001'
```

### 第三步：对照日志字段

```text
request_id=triage-500-001
method=GET
path=/items/999999
status=500
duration_ms=...
```

判断重点：

- 没有这条日志：请求可能没到目标服务，查代理、端口、路由或日志配置；
- 有请求日志但状态 4xx：优先检查输入、认证和权限，不要当成服务崩溃；
- 有状态 500：查同 request id 附近的异常堆栈和依赖调用；
- `duration_ms` 明显升高：关注数据库、下游服务、锁等待和资源压力；
- 只有 Uvicorn access 日志，没有应用上下文：补充应用日志或中间件字段。

## 5. CPU、内存、磁盘

### Linux

```bash
top
ps -eo pid,ppid,cmd,%cpu,%mem --sort=-%cpu | head
free -h
df -h
du -sh /var/log/* 2>/dev/null | sort -h | tail
```

### Windows PowerShell

```powershell
Get-Process | Sort-Object CPU -Descending | Select-Object -First 10
Get-CimInstance Win32_OperatingSystem |
    Select-Object FreePhysicalMemory,TotalVisibleMemorySize
Get-PSDrive -PSProvider FileSystem
Get-ChildItem .\logs -File -ErrorAction SilentlyContinue |
    Sort-Object Length -Descending | Select-Object -First 10
```

### 判断

```text
CPU 持续高 → 查热点接口、循环、序列化、数据库查询
内存持续增长 → 查泄漏、缓存、连接和大对象
磁盘接近满 → 查日志、报告、临时文件和数据库增长
```

单次采样不能证明资源异常，应观察一段时间并结合请求时间线。

## 6. 本项目日志格式

现在应用层会记录：

```text
request_id=<id> method=<method> path=<path> status=<code> duration_ms=<ms>
```

示例：

```text
request_id=triage-500-001 method=GET path=/items/999999 status=404 duration_ms=2.31
```

响应也会带回：

```text
X-Request-ID: triage-500-001
```

request id 只用于关联日志，不是 JWT，不是登录凭据，也不能替代鉴权。

## 7. 面试中的最短回答

> 线上出现问题时，我先用 curl 验证网络和 HTTP 响应，再用 ss/lsof 或 PowerShell 检查端口监听和占用进程，用 ps/top/free/df 判断进程和资源状态。对 500 我会携带或记录 request id，按 request id 查询应用日志和异常堆栈，再结合耗时字段判断是业务异常、数据库、下游依赖还是资源问题。定位结论必须由请求、日志和资源证据共同支持。

## 8. WSL Linux 实操

Windows 项目可以通过 WSL 挂载路径访问：

```bash
cd "/mnt/e/work/study/py-start/project-learn/03_实战宝库区/01_Web后端测开实战"
```

项目源代码可以共用，但 Python、虚拟环境、依赖、进程和端口属于不同运行环境。Linux 虚拟环境放在 Linux 主目录：

```bash
python3 -m venv ~/venvs/sdet-linux
source ~/venvs/sdet-linux/bin/activate
```

验证当前解释器：

```bash
which python
python --version
python -m pip --version
```

## 9. Linux 真实 HTTP 与端口进程定位

使用 `8001` 与 Windows 的 `8000` 分开，避免实验时混淆。端口端点由协议、本地 IP 和端口号共同确定：

```text
TCP + 127.0.0.1 + 8001
```

启动 Linux 服务后：

```bash
ss -ltnp | grep ':8001'
pgrep -af "uvicorn"
ps -p <PID> -o pid,ppid,stat,etime,%cpu,%mem,rss,vsz,cmd
```

`127.0.0.1` 是回环地址，只监听本机；`LISTEN` 表示端口正在监听；`PID` 用于继续定位进程命令和资源。

## 10. Linux 日志落盘与实时分析

让日志同时显示并保存：

```bash
mkdir -p ~/logs
python -m uvicorn ... 2>&1 | tee -a ~/logs/fastapi-wsl.log
```

查看与筛选：

```bash
grep 'request_id=<id>' ~/logs/fastapi-wsl.log
tail -n 20 ~/logs/fastapi-wsl.log
tail -f ~/logs/fastapi-wsl.log
grep -E 'status=4[0-9]{2}|status=5[0-9]{2}' ~/logs/fastapi-wsl.log
```

`tail -f` 适合在独立窗口持续观察新增日志；`tee` 使服务日志既显示在终端又保存到文件。管道后没有颜色通常是因为标准输出不再直接连接 TTY，属于正常现象。

## 11. 小样本日志统计判断

使用 request id 前缀筛选同一批请求，避免把历史日志混入：

```bash
grep -c 'request_id=stats-' ~/logs/fastapi-wsl.log
grep 'request_id=stats-' ~/logs/fastapi-wsl.log | grep -oE 'status=[0-9]{3}' | sort | uniq -c
```

可以使用 `awk -F'duration_ms='` 计算平均耗时和最大耗时。分析时必须说明：样本量、是否串行、是否本机环境、是否存在冷启动异常；不能把少量串行请求的平均耗时当作 QPS、P95 或生产容量结论。

## 12. 证据边界

当前项目的 502/504 是 `TestClient + pytest-mock` 的应用层故障映射测试，不是真实第三方支付网络。要测试真实 Uvicorn HTTP 下的下游 502/504，需要一个独立的 fake payment service；它是本地测试替身，不是真实第三方服务。该内容属于后续高级集成测试扩展。
