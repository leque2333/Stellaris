FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV CONSOLE_PORT=18890
ENV TZ=Asia/Shanghai
# 容器内必须监听 0.0.0.0，否则宿主端口映射进不来（默认只绑本机是给裸机用的）
ENV HUB_BIND=0.0.0.0

RUN apt-get update && apt-get install -y --no-install-recommends curl tzdata \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

# 安装智能体与时间轴运行所需的依赖（requirements-agent.txt 只是兼容占位，不能当安装清单）
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# 复制代码模块：仅包含 agent_core 与控制台
COPY agent_core /app/agent_core
COPY console /app/console

# 预创建数据挂载目录
RUN mkdir -p /app/data /app/backups

EXPOSE 18890

# 默认随机拉起控制台服务 (通过 console/server.py 驱动)
CMD ["python", "console/server.py"]
