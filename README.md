This repo is for the helper functions on the server

## Port

lappy-server: 8050

car-to-influx: 8085

InfluxDB: 8086

MangoDB: 3000 (not in this repo)

## No Port Assigned 

Slackbot



## Car-to-influx

Car to influx listeners for CAN frames from the car, and load it into Influx DB

## Server Endpoint

The server exposes a single HTTP endpoint for ingesting CAN messages:

```
POST http://3.98.181.12:8085/can
```

### Single Message

json

```json
{
  "messages": [
    {
      "id": "0x1A3",      // CAN ID as string (hex or decimal)
      "data": [10, 20, 30, 40, 50, 60, 70, 80],  // Data bytes as array of integers
      "timestamp": 1648123456.789  // Unix timestamp in seconds
    }
  ]
}
```

### Multiple Messages

json

```json
{
  "messages": [
    {
      "id": "0x1A3",
      "data": [10, 20, 30, 40, 50, 60, 70, 80],
      "timestamp": 1648123456.789
    },
    {
      "id": "26",         // Decimal ID also accepted
      "data": [1, 2, 3, 4, 5, 6, 7, 8],
      "timestamp": 1648123456.790
    }
  ]
}
```