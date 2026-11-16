# Server-only image (the server uses just the Python standard library).
#   docker build -t kartarena .
#   docker run -d --name kartarena -p 5555:5555/tcp -p 5556:5556/udp -v kart-data:/app/data kartarena
FROM python:3.12-slim
WORKDIR /app
COPY common ./common
COPY server ./server
COPY run_server.py .
EXPOSE 5555/tcp 5556/udp
VOLUME /app/data
CMD ["python", "-u", "run_server.py", "--data", "/app/data", "--name", "Turbo Kart Arena (docker)"]
