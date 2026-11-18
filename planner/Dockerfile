FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
COPY planner/requirements.txt planner/requirements.txt
RUN pip install --no-cache-dir -r planner/requirements.txt
COPY planner/ planner/
COPY data/route-timetables.ts data/route-timetables.ts
COPY .railblock/ .railblock/
ENV RAILBLOCK_BIND_HOST=0.0.0.0 PORT=8008 PYTHONDONTWRITEBYTECODE=1
EXPOSE 8008
CMD ["python", "-u", "planner/server.py"]
