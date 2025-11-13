# Lappy

`lappy` is a Dash web application used for lap time analysis and visualisation.

## Ports

- Host port **8050** maps to the Dash server running inside the container.

## Configuration

The service mounts the entire `installer/lappy/` directory into the container. Update files in that folder to change the UI, then restart the container.

## Development tips

- Edit Python files locally; the volume mount reloads code on container restart.
- Inspect logs with `docker compose logs -f lappy` if the UI fails to start.
- Add new Python dependencies to `installer/lappy/requirements.txt` and rebuild the image (`docker compose build lappy`).
