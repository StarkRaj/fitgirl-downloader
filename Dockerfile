FROM python:3.11-slim

# Install Tor
RUN apt-get update && apt-get install -y tor

WORKDIR /app

# 1. Copy only requirements first
COPY requirements.txt .

# 2. Install dependencies (cached layer)
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

RUN chmod +x start.sh

EXPOSE 8000

CMD ["bash", "start.sh"]