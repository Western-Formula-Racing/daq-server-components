```
docker run -d \
  --name slackbot \
  --restart unless-stopped \
  --cpus="1.0" \
  --memory="700m" \
  --memory-swap="1.2g" \
  -v ~/slackbot:/app \
  slackbot
```

