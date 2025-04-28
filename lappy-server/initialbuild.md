```docker run -d \
  --name lappy-server \
  --restart always \
  --cpus="0.25" \
  --cpu-shares=2048 \
  -v /home/ubuntu/lappy-server:/app \
  lappy-server \
  python lap.py```
