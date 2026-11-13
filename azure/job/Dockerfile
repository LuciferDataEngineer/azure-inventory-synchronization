FROM python:3.12-slim-bookworm
WORKDIR /app
COPY azure/job/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY azure/shared/contract.py azure/job/produce.py data/workload.jsonl ./
COPY third_party/CosmosConf26QueryDemos-LICENSE.txt /app/NOTICE-CosmosConf26QueryDemos.txt
USER 65534
ENTRYPOINT ["python", "produce.py"]
