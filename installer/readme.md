Expected Structure for Installer

project-root/
├── docker-compose.yml
├── .env                         # for secrets like SLACK_BOT_TOKEN, DB creds
│
├── frontend-build/              # built React/Vue/Angular frontend output
│   └── index.html
│
├── car-to-influx/
│   ├── Dockerfile
│   ├── listener.py
│   ├── requirements.txt
│   └── utils/                   # (optional) helper Python modules
│
├── slackbot/
│   ├── Dockerfile
│   ├── slack_bot.py
│   ├── requirements.txt
│   └── helpers/                 # (optional) extra code
│
├── lappy/
│   ├── Dockerfile
│   ├── lap.py
│   ├── requirements.txt
│   └── modules/                 # (optional) reusable code
│
├── artifact-viewer/             # only needed if you build your own image
│   ├── Dockerfile
│   └── app/                     # (if you have source here)
│
├── scripts/                     # helper bash/python scripts for setup
│   ├── init_influx.sh
│   ├── init_grafana.sh
│   └── data_loader.py
│
└── README.md