# 安全总览

> 操作手册见 [API_SECURITY.md](API_SECURITY.md)；改代码前先看 [DEV_RULES.md](DEV_RULES.md)。
> 本文回答三个问题：**防谁、怎么防、哪里还没防住**。

---

## 一、信任边界

```text
            互联网
              │
        ┌─────▼─────┐   TLS（18891 / 53353）
        │   nginx   │   X-Forwarded-For 在末尾追加真实来源
        └─────┬─────┘
              │ 127.0.0.1:18895（明文 HTTP）
        ┌─────▼──────────────────────────────────┐
        │            Stellaris 中枢              │
        │  ① 来源白名单  ② 请求签名  ③ 登录会话   │
        └─────┬──────────────────────────────────┘
              │ 同一个信任圈（docker / 局域网）
        ┌─────▼─────┐        ┌──────────────┐
        │  AstrBot  │        │ 本地脚本/CLI │
        └───────────┘        └──────────────┘
```

**信任圈内**（本机、docker 网桥、局域网）默认不参与签名——它们本来就能直连 18895，
加签名只是自我安慰；**信任圈外**（经 nginx 进来的公网流量）必须过三道关。

---

## 二、六层防护

| 层 | 机制 | 默认 | 在哪 |
|---|---|---|---|
| ① 传输 | nginx TLS | 已启用 | `nginx.conf` 18891/53353 |
| ② 来源 | `local_only` + `allow_cidrs` | 关 | 控制台「API 安全」/ `data/api_security.json` |
| ③ 完整性 | HMAC 签名 + 时间戳防重放 | 关 | 同上，作用于 `/v1` `/api/v1` `/hub/v1` |
| ④ 凭据 | 客户端 Key（`data/clients.json`）+ 会话 Cookie | 开 | `agent_core/clients.py`、`agent_core/auth.py` |
| ⑤ 防爆破 | 登录失败 IP 限速：10 分钟 5 次锁 10 分钟 | 开 | `api_security.LoginThrottle` |
| ⑥ 可追溯 | 审计日志 `logs/audit-*.log` | 开 | `agent_core/audit_log.py` |

### ① 传输

中枢自己只监听明文 HTTP（`0.0.0.0:18895`），TLS 由 nginx 终结。
**这意味着同网段可以直接绕过 nginx 直连 18895**，见「已知短板 1」。

### ② 来源

```text
client_ip = 从 X-Forwarded-For 右往左跳过 trusted_proxies，取第一个不可信地址
```

不能取最左边：nginx 用 `$proxy_add_x_forwarded_for` 是**在末尾追加**真实来源，
最左边可能是客户端自己伪造的（2026-10-31 修）。

### ③ 签名

```text
canonical = METHOD \n PATH \n TIMESTAMP \n SHA256(body)
signature = HMAC_SHA256(secret, canonical)
```

校验顺序：当前密钥 → 过渡期内的旧密钥 → 拒绝。窗口 300 秒。
`exempt_cidrs` 默认覆盖整个内网，所以 **AstrBot 零改动**。

### ④ 凭据

- 模型接口：`Authorization: Bearer <客户端 Key>` + `X-User-Id`（要在白名单里，否则 403 `guest_disabled`）
- 控制台：`anan_session` Cookie，`HttpOnly`，HTTPS 下带 `Secure`，`SameSite=Lax`
- CORS 只对**同源**请求回 `Access-Control-Allow-Origin`（2026-10-31 修）

### ⑤ 防爆破

```json
{"success": false, "error": "too_many_attempts", "retry_after": 599}
```

HTTP 429，状态在内存里（重启清零），登录成功立刻解除。

### ⑥ 可追溯

`logs/audit-YYYY-MM-DD.log`，JSON Lines，事件见 API_SECURITY §2.7。
保留 90 天 / 总量 256 MB / 7 天后 gzip。**不写时间轴**（时间轴是给 AI 的记忆）。

---

## 三、已知短板（诚实清单）

| # | 问题 | 影响 | 建议 |
|---|---|---|---|
| 1 | 18895 明文监听 `0.0.0.0` | 同网段可绕过 nginx 直连，跳过 TLS | 开 `local_only`，或用防火墙只放行本机/nginx |
| 2 | 密码哈希是单轮 `SHA-256(salt:password)` | `auth_config.json` 泄露后离线爆破很快 | 换 PBKDF2/bcrypt，写迁移 |
| 3 | 默认密码 `admin888`（未设 `HUB_AUTH_PASSWORD` 时） | 忘记改就是弱口令 | 部署后第一件事改密码；用 `python -c "from agent_core.auth import AuthManager; print(AuthManager('.').verify_credentials('admin','admin888'))"` 自查 |
| 4 | `require_key: false` 时匿名 `/v1` 按默认主控放行 | 任何能连到 18895 的进程都能用你的额度聊天、并以 master 身份写入时间轴 | 把 `data/clients.json` 的 `require_key` 设为 `true`（已认证客户端本来就有 120/60 次每分钟的限流） |
| 5 | 审计日志是本机文件 | 拿到 root 可以改历史 | 需要时外发到远端 syslog/对象存储 |
| 6 | 将来加 CDN 时 | CDN 回源 IP 会被当成客户端 | 把 CDN 网段加进 `trusted_proxies` |
| 7 | 会话 7 天有效 | 没有"一键踢下线" | 改密码时轮换 `sessions` 表 |

已修（2026-10-31 同批）：
- CORS 反射任意 Origin → 改为只认同源
- Cookie 缺 `HttpOnly` / `Secure` → 补上
- `X-Forwarded-For` 取最左（可伪造）→ 改为从右往左跳过可信代理

---

## 三·五、没有反向代理时

直接访问 `http://<内网IP>:18895`（不经过 nginx）时，各层是否还生效：

| 能力 | 有 nginx | 直连中枢 | 说明 |
|---|---|---|---|
| TLS | ✅ | ❌ | 中枢只监听明文，没有 TLS |
| Cookie `Secure` | ✅ 自动 | ❌ 不会加 | 明文下加了反而登不上 |
| `local_only` | ✅ 依赖 XFF | ✅ 直接看 peer | 直连时 XFF 一律忽略，伪造无效 |
| 请求签名 | ✅ | ✅ | 与传输无关；内网仍按 `exempt_cidrs` 豁免 |
| 登录限速 | ✅ 依赖 XFF | ✅ 直接看 peer | 直连的 IP 更准 |
| CORS 同源 | ✅ | ✅ | 比对 Origin 与 Host |
| 审计日志 | ✅ | ✅ | 与入口无关 |

**结论：应用层（来源、签名、限速、CORS、审计）不依赖反代，全都生效；
唯一失效的是传输加密。**

所以没有反代时，按暴露范围选一种：

```powershell
# A. 只给本机用（最省事）：绑回环，外部根本连不上
set HUB_BIND=127.0.0.1
python console\server.py 18895

# B. 要给 docker 里的 AstrBot 用：保持 0.0.0.0，但开本机/内网限制
python scripts\setup_security.py --enable-local-only --lan

# C. 要给外网用但没有 nginx：至少
python scripts\setup_security.py --enable-local-only   # 只放行指定网段
python scripts\setup_security.py --enable-signature    # 再把 exempt_cidrs 收窄
```

C 方案要额外注意：把 `signature.exempt_cidrs` 从默认的内网段改成只留 docker 网段，
否则局域网客户端全部免签名。

远程访问没有反代时，更稳的做法是走 SSH 隧道或 Tailscale，而不是把 18895 直接暴露。

`connect_astrbot.py` 会自动探测本机有没有反代（18890/18891/53353/443 是否在监听），并打印当前部署形态的建议；没有反代且 AstrBot 走本机回环时，它会提示把监听地址改成 `127.0.0.1`。监听地址也可以直接写在配置里：

```json
{"bind_host": "127.0.0.1"}
```

优先级：环境变量 `HUB_BIND` > 配置文件 `bind_host` > `0.0.0.0`。改完需要重启中枢。

## 四、开启顺序（新部署）

```powershell
# 1) 接入 AstrBot，顺手生成安全配置（密钥已生成，开关默认关）
python scripts\connect_astrbot.py

# 2) 只允许内网访问（含 docker 网段，AstrBot 不受影响）
python scripts\setup_security.py --enable-local-only --lan

# 3) 打开签名（内网自动豁免）
python scripts\setup_security.py --enable-signature

# 4) 需要换密钥时
python scripts\setup_security.py --rotate-secret --grace-minutes 30

# 5) 看审计
curl.exe "http://127.0.0.1:18895/api/audit?limit=50" -H "X-Session-Token: <token>"
```

## 四·五、部署自检

三个入口：**控制台按钮**、**命令行**、**重启后自动跑一次**。

```text
控制台：设置 → 运行日志 → 部署自检   （结果直接显示在文本框里）
命令行：python scripts\check_deploy.py
启动时：中枢起来后后台跑一次，只在有失败项时弹错误消息并写审计
```

```powershell
python scripts\check_deploy.py
```

它会逐项检查并给结论（退出码非 0 表示有失败项）：

```text
✓ 中枢端口       127.0.0.1:18895 在监听
✓ docker 挂载   /AstrBot/data ← D:\机器人_完整版\astrbot\data
✓ 安全配置       bind=0.0.0.0 local_only=False 签名=关
✓ 客户端 Key    require_key=true，3 个客户端 / 3 个 Key
✓ 审计日志       ... 2 个文件 · 4.7 KB · 保留 90 天
```

重点看两条：

1. **docker 挂载**：目录改名后，容器挂载源可能消失，容器里变成悬空挂载
   （`ls` 报 No such file），AstrBot 静默不工作
2. **来源白名单**：`local_only` 开着但没放行 `172.16.0.0/12` 时，
   docker 里的 AstrBot 会被 403

第 2 条在 `setup_security.py` 里也会主动提醒（检测到 astrbot 容器时）。

## 五、回滚

```powershell
# 最快：删配置，回到全关
Remove-Item data\api_security.json
# 或
python scripts\setup_security.py --disable-local-only --disable-signature
```

如果 local_only 把自己锁在门外，直接删文件（配置热加载，不用重启）。

---

## 六、文件索引

| 文件 | 作用 |
|---|---|
| `agent_core/api_security.py` | 配置读写、IP 判定、签名校验、登录限速 |
| `agent_core/audit_log.py` | 审计日志写入/读取/保留策略 |
| `agent_core/auth.py` | 会话与密码校验 |
| `agent_core/clients.py` | 客户端 Key 与用户白名单 |
| `console/server.py` | 5 个 `do_*` 入口的 `_access_blocked` / `_signature_blocked` |
| `scripts/setup_security.py` | 初始化 / 开关 / 轮换命令行 |
| `scripts/connect_astrbot.py` | 一键接入 AstrBot（含安全配置初始化） |
| `data/api_security.json` | 运行时配置（热加载） |
| `logs/audit-*.log` | 审计日志（90 天 / 256 MB） |
