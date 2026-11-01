# API 安全：本机访问限制 + 请求签名

两个开关都在 `data/api_security.json` 里，**默认全关**（不改变现有调用方式）。
文件改了会自动生效（按 mtime 重载），不用重启中枢。

```json
{
  "bind_host": "127.0.0.1",
  "local_only": false,
  "allow_cidrs": ["127.0.0.1/32", "::1/128"],
  "trusted_proxies": ["127.0.0.1/32", "::1/128"],
  "signature": {
    "enabled": false,
    "window_seconds": 300,
    "secret": "",
    "exempt_cidrs": ["127.0.0.0/8", "::1/128", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]
  }
}
```

| 字段 | 作用 |
|---|---|
| `bind_host` | 监听地址，默认 `127.0.0.1`（只给本机用）；要让局域网/反代访问改成 `0.0.0.0`，改完要重启 |
| `local_only` | 只允许本机访问。开启后不在 `allow_cidrs` 的客户端一律 403 |
| `allow_cidrs` | 本机模式下放行的网段。默认只有回环；要让局域网/容器访问就加网段 |
| `trusted_proxies` | 只有这些来源的 `X-Forwarded-For` 才可信（默认本机 nginx） |
| `signature.enabled` | 对 `/v1/*`、`/api/v1/*`、`/hub/v1/*` 校验签名 |
| `signature.window_seconds` | 时间戳容差，默认 300 秒（30~3600） |
| `signature.secret` | 共享密钥；**开了签名但密钥为空时会放行**（避免把自己锁死） |
| `signature.exempt_cidrs` | 这些来源不参与签名。默认整个内网（本机 / docker / 局域网）豁免 |

---

## 一、只允许本机访问

控制台路径：**设置 → 常规与系统 → 安全防护 → API 安全**（本机开关 / 放行网段 / 签名开关 / 密钥生成都在这里）。

也可以用接口：

开启：

```powershell
curl.exe -X POST http://127.0.0.1:18895/api/access_control `
  -H "Content-Type: application/json" `
  -H "X-Session-Token: <控制台 token>" `
  -d '{"local_only": true}'
```

查看当前状态（密钥只报"是否已配置"）：

```powershell
curl.exe http://127.0.0.1:18895/api/access_control -H "X-Session-Token: <token>"
```

### 为什么认 X-Forwarded-For

nginx 反代时，中枢看到的来源都是 `127.0.0.1`。若只看 socket 地址，
开了"只允许本机"等于没开。所以：

```text
socket 来源在 trusted_proxies 里  → 用 X-Forwarded-For 第一跳作为客户端 IP
否则                              → 用 socket 来源
```

nginx 配置里已经带了 `proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;`，
不用再改。

### 注意

- **AstrBot 跑在 Docker 里**时，它的来源是 docker 网桥（如 `172.17.0.1`）。
  开 `local_only` 后要么把这个网段加进 `allow_cidrs`，要么让 AstrBot 走 host 网络。
- 开启后**控制台从外网也打不开**（403），这是这个开关的本意。

---

## 一·五、初始化脚本

```powershell
# 只建配置（签名密钥先生成好，开关保持关闭）
python scripts\setup_security.py --generate-secret

# 开本机限制，并把局域网 / docker 网段放行
python scripts\setup_security.py --enable-local-only --lan

# 开签名（内网仍豁免，AstrBot 不用动）
python scripts\setup_security.py --enable-signature

# 看当前状态
python scripts\setup_security.py --show
```

`scripts/connect_astrbot.py` 在接入 AstrBot 时会自动调用同一套初始化。

## 二、请求签名（HMAC + 防重放）

### 规则

```text
canonical  = METHOD + "\n" + PATH + "\n" + TIMESTAMP + "\n" + SHA256_HEX(RAW_BODY)
signature  = HMAC_SHA256(secret, canonical) 的小写 hex

请求头：
  X-Timestamp: <unix 秒>
  X-Signature: <hex>
```

- `PATH` 是**带 query 的原始请求目标**（例如 `/v1/models`、`/hub/v1/archives?x=1`）
- `RAW_BODY` 是请求体原始字节；GET 用空字节（`sha256(b"")`）
- 时间戳与服务器时间差超过 `window_seconds` 直接拒绝（防重放）

### 客户端示例（Python）

```python
import hashlib, hmac, json, time, urllib.request

SECRET = "<和中枢配置里一致的 secret>"
body = json.dumps({"model": "gemini-3.8-flash",
                   "messages": [{"role": "user", "content": "在吗"}]}).encode()
ts = str(int(time.time()))
canonical = "\n".join(["POST", "/v1/chat/completions", ts, hashlib.sha256(body).hexdigest()])
sig = hmac.new(SECRET.encode(), canonical.encode(), hashlib.sha256).hexdigest()

req = urllib.request.Request(
    "http://127.0.0.1:18895/v1/chat/completions", data=body, method="POST",
    headers={"Content-Type": "application/json", "Authorization": "Bearer <客户端 Key>",
             "X-Timestamp": ts, "X-Signature": sig,
             "X-User-Id": "10001", "X-Channel": "qq"},
)
print(urllib.request.urlopen(req, timeout=60).read()[:80])
```

### 失败返回

```json
{"ok": false, "error": "missing_signature"}   // 没带两个头
{"ok": false, "error": "stale_timestamp"}     // 时间戳过期
{"ok": false, "error": "bad_signature"}       // 签名不对
{"ok": false, "error": "bad_timestamp"}       // 时间戳不是数字
```

HTTP 状态都是 401。

### 为什么 AstrBot 不用改

AstrBot 跑在 docker / 局域网里，来源 IP 落在 `signature.exempt_cidrs`（RFC1918 + 回环），
**签名校验直接跳过**。这样"签名"只保护经 nginx 进来的公网流量，内部客户端零改动。

初始化也是自动的：

```text
python scripts/connect_astrbot.py       # 一键接 AstrBot 时顺带生成 data/api_security.json
python scripts/setup_security.py --show # 单独查看
```

如果哪天 AstrBot 从公网绕进来（不经过内网），把它的网段从 `exempt_cidrs` 里去掉即可。

### 开着签名要改谁

```text
AstrBot provider 的请求        需要加签名头（或把该客户端换成走 local_only 的本地回环）
hub-tools 薄壳（/api/tools/clean）  不在签名范围内（它自带客户端 Key 校验）
控制台 /api/*                  不在签名范围内（走 session）
```

---

## 二·五、密钥轮换

`signature.secret` 是当前密钥，`previous_secret` 是上一个，在 `previous_until` 之前仍然有效。

```powershell
# 换新密钥，旧密钥保留 30 分钟（分布式部署留出更新时间）
python scripts\setup_security.py --rotate-secret --grace-minutes 30
```

控制台里点「轮换」也一样，**新密钥只在弹窗里显示一次**，记得复制。

签名校验顺序：当前密钥 → 未过期的旧密钥 → 拒绝。

## 二·六、登录失败限速

`/api/auth/login` 按 IP 统计：10 分钟内输错 5 次，锁 10 分钟。

```json
{"success": false, "error": "too_many_attempts", "retry_after": 587}
```

HTTP 状态 429。锁定状态在内存里，重启中枢即清零；登录成功会立刻解除。

## 二·七、审计日志

安全事件不写时间轴（那是给 AI 的记忆），单独落 `logs/audit.log`，JSON Lines：

| 事件 | 何时记 |
|---|---|
| `login_ok` / `login_failed` | 登录成功 / 密码错 |
| `login_blocked` | 触发限速被拒（429） |
| `security_changed` | 改 local_only / 白名单 / 签名开关 |
| `secret_rotated` | 轮换签名密钥 |
| `access_denied` | 本机模式挡住了某来源（403） |
| `signature_rejected` | 签名缺失 / 过期 / 不对（401） |

查看：

```powershell
curl.exe "http://127.0.0.1:18895/api/audit?limit=100" -H "X-Session-Token: <token>"
```

控制台：**设置 → 运行日志 → 安全日志** 按钮，直接看最近 500 条。

### 保留策略

```text
文件名      logs/audit-YYYY-MM-DD.log（按天分文件）
单文件上限  5 MB，超了切 audit-日期.1.log（每天最多 3 段）
压缩        7 天前的明文压成 audit-日期.log.gz，读取时自动解压
保留天数    90 天，更旧的自动删
总量上限    256 MB，超了从最旧的文件开始删
```

| 环境变量 | 默认 | 作用 |
|---|---|---|
| `HUB_AUDIT_DIR` | `logs/` | 换目录 |
| `HUB_AUDIT_RETENTION_DAYS` | `90` | 保留天数 |
| `HUB_AUDIT_MAX_TOTAL_MB` | `256` | 总量上限 |
| `HUB_AUDIT_MAX_FILE_MB` | `5` | 单文件上限 |
| `HUB_AUDIT_COMPRESS_AFTER_DAYS` | `7` | 多少天前的日志压缩成 .gz（0 表示不压缩） |

清理动作在每次写入后触发，但**最多每小时跑一次**，不会每条日志都扫目录。
旧的单文件 `audit.log` 会在中枢启动时自动收编成 `audit-日期.log`，不丢历史。

其它细节：
- 同一 `(事件, IP, 错误码)` 60 秒内只记一次，防止被 401 刷爆磁盘
- 配置变更 / 密钥轮换 / 登录成功**不去重**，保证每次操作都有记录
- 控制台「安全日志」顶部会显示：条数 · 文件数 · 占用 · 保留天数
- 压缩只影响磁盘占用，`/api/audit` 读取行为不变（跨文件、跨压缩包拼接）

## 三、回滚

改坏了最省事的办法：把 `data/api_security.json` 删掉（回到默认全关），或：

```powershell
curl.exe -X POST http://127.0.0.1:18895/api/access_control `
  -H "Content-Type: application/json" -H "X-Session-Token: <token>" `
  -d '{"local_only": false, "signature": {"enabled": false}}'
```

注意：**如果已经开了 local_only 且把自己挡在外面**，控制台也进不去，
这时直接删文件最快（配置是热加载的）。

---

## 四、实现位置

| 文件 | 作用 |
|---|---|
| `agent_core/api_security.py` | 配置读写、IP 判定、签名校验（纯函数，好测） |
| `console/server.py` | `_access_blocked()` / `_signature_blocked()` 挂在 5 个 `do_*` 入口 |
| `data/api_security.json` | 运行时配置（在 `.gitignore` 的 `data/` 下） |
| `tests/test_api_security.py` | 15 个用例：签名、过期、重放、CIDR、守卫接线 |
