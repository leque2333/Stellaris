FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV CONSOLE_PORT=18890
ENV TZ=Asia/Shanghai

RUN apt-get update && apt-get install -y --no-install-recommends curl tzdata \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

# 安装智能体与时间轴运行所需的最小依赖
COPY requirements-agent.txt /app/
RUN pip install --no-cache-dir -r requirements-agent.txt

# 复制代码模块：仅包含 agent_core 与控制台
COPY agent_core /app/agent_core
COPY console /app/console

# 预创建数据挂载目录
RUN mkdir -p /app/data /app/backups

EXPOSE 18890
EXPOSE 18888

# 默认随机拉起控制台服务 (通过 console/server.py 驱动)
CMD ["python", "console/server.py"]
