# Frontend

The `frontend` service serves the static production build of the DAQ landing page using Nginx.

## Ports

- Host port **8060** maps to Nginx port **80**.

## Assets

Files are baked into `installer/frontend-build/`. Replace the contents of that directory with a new build (`npm run build` output) to update the site.

## Customisation

- Update `installer/docker-compose.yml` if you need to expose the site on a different host port.
- Mount additional assets by extending the service definition in a `docker-compose.override.yml` file.

## Development tips

Develop the frontend separately, generate a production build, and copy the build artifacts into `installer/frontend-build/` before restarting the compose stack.
