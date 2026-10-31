# API 安全：本机访问限制 + 请求签名

两个开关都在 `data/api_security.json` 里，**默认全关**（不改变现有调用方式）。
文件改了会自动生效（按 mtime 重载），不用重启中枢。

```json
{
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
             "X-User-Id": "2986371638", "X-Channel": "qq"},
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
