FROM python:3.9-slim

# 安装PostgreSQL客户端
RUN apt-get update && apt-get install -y \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 添加等待脚本
COPY wait-for-postgres.sh .
RUN chmod +x wait-for-postgres.sh

# 等待PostgreSQL就绪后再启动应用
CMD ["./wait-for-postgres.sh", "python", "main.py"] 