```
docker run -d \
 --name slackbot \
 --restart always \
 --cpus="0.25" \
 -v /home/ubuntu/slackbot:/app \
 -e SLACK_BOT_TOKEN=YOUR_SLACK_BOT_TOKEN \
 -e SLACK_APP_TOKEN=YOUR_SLACK_APP_TOKEN \
 slackbot \
 python slack_bot.py
```

