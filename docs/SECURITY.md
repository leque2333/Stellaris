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
| 3 | 默认密码 `admin888`（未设 `HUB_AUTH_PASSWORD` 时） | 忘记改就是弱口令 | 部署后第一件事改密码；`check_no_secrets` 已覆盖源码层 |
| 4 | `/v1` 没有限速 | 持有 Key 的客户端可以高频刷 | 按 Key 做令牌桶；账本已有用量数据 |
| 5 | 审计日志是本机文件 | 拿到 root 可以改历史 | 需要时外发到远端 syslog/对象存储 |
| 6 | 将来加 CDN 时 | CDN 回源 IP 会被当成客户端 | 把 CDN 网段加进 `trusted_proxies` |
| 7 | 会话 7 天有效 | 没有"一键踢下线" | 改密码时轮换 `sessions` 表 |

已修（2026-10-31 同批）：
- CORS 反射任意 Origin → 改为只认同源
- Cookie 缺 `HttpOnly` / `Secure` → 补上
- `X-Forwarded-For` 取最左（可伪造）→ 改为从右往左跳过可信代理

---

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
