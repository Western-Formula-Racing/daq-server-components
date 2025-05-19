```docker run -d \
docker run -d \
  --name lappy-server \
  --restart always \
  --cpus="0.25" \
  --cpu-shares=2048 \
  -p 8050:8050 \
  --network datalink \
  lappy-server
```



