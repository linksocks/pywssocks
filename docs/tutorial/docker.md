# Docker and Docker Compose Deployment

## Docker Example

Below is an example of how to run Pywssocks using Docker.

### Forward Proxy

**Server Side:**

```bash
docker run --rm -it \
    -p 8765:8765 \
    jackzzs/pywssocks \
    server -t example_token
```

**Client Side:**

```bash
docker run --rm -it \
    -p 1080:1080 \
    jackzzs/pywssocks \
    client -t example_token -u ws://localhost:8765 -p 1080
```

### Reverse Proxy

**Server Side:**

```bash
docker run --rm -it \
    -p 8765:8765 \
    -p 1080:1080 \
    jackzzs/pywssocks \
    server -t example_token -p 1080 -r
```

**Client Side:**

```bash
docker run --rm -it \
    jackzzs/pywssocks \
    client -t example_token -u ws://localhost:8765 -r
```

## Docker Compose Example

Using Docker Compose simplifies the management of Pywssocks services by defining them in a YAML file.

You should install [Docker Compose](https://docs.docker.com/compose/install/) first.

### Forward Proxy

**Server Side:**

```yaml title="docker-compose.yaml"
version: '3.8'
services:
  pywssocks-server:
    container_name: pywssocks-server
    image: jackzzs/pywssocks
    restart: unless-stopped
    command: server -t example_token
    ports:
      - "8765:8765"
```

**Client Side:**

```yaml title="docker-compose.yaml"
version: '3.8'
services:
  pywssocks-client:
    container_name: pywssocks-client
    image: jackzzs/pywssocks
    restart: unless-stopped
    command: client -t example_token -u ws://server:8765 -p 1080
    ports:
      - "1080:1080"
```

### Reverse Proxy

**Server Side:**

```yaml title="docker-compose.yaml"
version: '3.8'
services:
  pywssocks-server:
    container_name: pywssocks-server
    image: jackzzs/pywssocks
    restart: unless-stopped
    command: server -t example_token -p 1080 -r
    ports:
      - "8765:8765"
      - "1080:1080"
```

**Client Side:**

```yaml title="docker-compose.yaml"
version: '3.8'
services:
  pywssocks-client:
    container_name: pywssocks-client
    image: jackzzs/pywssocks
    restart: unless-stopped
    command: client -t example_token -u ws://server:8765 -r
```

### Run Docker Compose Stack

You can start the services using:

```bash
docker-compose up -d
```

And stop them using:
```bash
docker-compose down
```

View service logs:
```bash
docker-compose logs -f 
```

Update to the latest version:
```bash
docker-compose pull && docker-compose up -d
```

!!! note

    Make sure to run all commands in the directory containing your `docker-compose.yml` file.