# 身份、白名单与访问策略

## 1. 模型

```text
档案 = 一个人
身份 = 平台账号
白名单 = archive_identities 里有记录
```

只有白名单身份才有档案、长期记忆、事实、图关系和自定义好感度。

```text
没有身份记录
→ 不是档案
→ 默认访客策略
```

## 2. 档案级别

当前档案只有两种角色：

```text
admin    管理员
guest    访客档案
```

新建档案时选择：

```text
POST /api/archives
{
  "display_name": "主人",
  "role": "admin",
  "platform": "qq",
  "user_id": "<你的QQ>"
}
```

- `admin`：可访问管理接口，后续可扩展权限；
- `guest`：白名单成员，但有档案与记忆，只是不具备管理权限。

注意：

```text
“访客档案”≠“临时访客”
```

- 访客档案：在白名单里，有档案；
- 临时访客：不在白名单，没有档案，默认 50。

## 3. 访问策略

配置文件：

```text
data/access_policy.json
```

默认：

```json
{
  "guest_enabled": false,
  "guest_timeline_enabled": false,
  "guest_default_affection": 50
}
```

Web：

```text
设置 → 常规与系统 → 访问策略
```

两个开关：

```text
临时访客            默认关闭
访客写入时间轴      默认关闭
```

### guest_enabled = false

非白名单请求：

```text
403 guest_disabled
```

AstrBot 不会收到回复。

### guest_enabled = true

非白名单请求：

```text
无档案
无长期事实
无图关系
好感度固定 50
```

### guest_timeline_enabled = true

只有开启临时访客后才有意义：

```text
访客对话写入时间轴
person_id = guest_<platform>_<uid>
```

不写入档案，不参与档案召回。

## 4. 解析流程

```text
platform + UID
→ archive_identities 精确查询
  命中 → 档案 / 记忆 / 好感度
  未命中
     guest_enabled=false → 403
     guest_enabled=true  → 临时访客
```

不按：

```text
昵称
头像
群号
UMO
```

## 5. 添加白名单

Web：

```text
档案 → 某个档案 → 添加身份
平台：qq / bilibili
UID：平台稳定 ID
```

或 API：

```text
POST /hub/v1/archives/{archive_id}/identities
{
  "platform": "bilibili",
  "user_id": "<B站UID>",
  "display_name": "主人2333"
}
```

## 6. 当前状态

线上只保留一个管理员档案：

```text
arc_master  主人  admin
```

平台身份：

```text
qq       <你的QQ>
bilibili <B站UID>
```

兼容别名：<你的QQ> → arc_master（旧数据迁移后仍可用旧 ID 访问）。

临时访客与访客时间轴均默认关闭。

## 7. 后续权限

`archives.role` 已可作为权限入口：

```text
admin
guest
```

后续新增权限时建议：

```text
archive_roles
role_permissions
```

不要继续在代码里写死 `role == "admin"` 的判断；先收敛成权限表。
