# Startup Data Loader

This Docker container automatically loads CSV telemetry data into InfluxDB during system startup.

## How it works

1. **Data Source**: Reads CSV files from `/data` directory (mounted from `../data`)
2. **DBC File**: Uses `WFR25.dbc` file included in the container to decode CAN messages
3. **Processing**: Parses CSV files with timestamp format `YYYY-MM-DD-HH-MM-SS.csv`
4. **Upload**: Streams data to InfluxDB bucket `ourCar` in organization `WFR`
5. **Completion**: Container exits after processing all files

## CSV Format Expected

```
relative_ms,protocol,can_id,byte0,byte1,byte2,byte3,byte4,byte5,byte6,byte7
506,CAN,176,0,0,6,0,0,0,2,0
507,CAN,2048,0,0,0,0,0,0,0,0
...
```

## Environment Variables

- `INFLUXDB_TOKEN`: InfluxDB authentication token (automatically provided)

## Files

- `Dockerfile`: Container definition
- `requirements.txt`: Python dependencies
- `load_data.py`: Main data loading script
- `WFR25.dbc`: CAN database file for message decoding

## Usage

This container is automatically started as part of the DAQ system startup process. It will:

1. Wait for InfluxDB to be ready
2. Process all CSV files in the data directory
3. Upload decoded CAN data to InfluxDB
4. Exit when complete

## Monitoring

Check container logs:
```bash
docker logs startup-data-loader
```

The container will show progress and completion status for each CSV file processed.
