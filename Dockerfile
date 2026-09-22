FROM python:3.12-slim

WORKDIR /app

# No third-party dependencies: remote_server.py and pharmacy_logic.py
# use only the Python standard library.
COPY pharmacy_logic.py remote_server.py ./

# Cloud Run injects PORT at runtime; 8080 is just the local-testing default.
ENV PORT=8080
EXPOSE 8080

CMD ["python", "remote_server.py"]
