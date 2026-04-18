FROM python:3.11-slim

# Install Tor
RUN apt-get update && apt-get install -y tor

WORKDIR /app

COPY . .

RUN pip install --no-cache-dir -r requirements.txt

RUN chmod +x start.sh

CMD ["bash", "start.sh"]