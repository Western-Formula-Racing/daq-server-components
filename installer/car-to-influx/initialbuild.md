docker run -d \
  --name car-to-influx \
  --restart always \
  -v /home/ubuntu/car-to-influx:/app \
  -p 8085:8085 \
  --cpu-shares 4096 \
  car-to-influx