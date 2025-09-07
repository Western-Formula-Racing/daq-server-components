```
docker run -d \
 --name slackbot \
 --restart always \
 --cpus="0.25" \
 -v /home/ubuntu/slackbot:/app \
 -e SLACK_BOT_TOKEN=xoxb-52272542916-8790672291073-1rZVA8yhIkt9OZy2jdITkvPQ \
 -e SLACK_APP_TOKEN=xapp-1-A08P01YR7M0-8764649895847-57581196f6add241ae4bba782b045005a15400c27f9388178fdba8409caf0219 \
 slackbot \
 python slack_bot.py
```

