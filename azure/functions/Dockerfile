FROM mcr.microsoft.com/azure-functions/python:4-python3.12-appservice
ENV AzureWebJobsScriptRoot=/home/site/wwwroot AzureFunctionsJobHost__Logging__Console__IsEnabled=true
COPY azure/shared/install_odbc.sh /tmp/install_odbc.sh
RUN sh /tmp/install_odbc.sh
WORKDIR /home/site/wwwroot
COPY azure/functions/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY azure/shared/contract.py azure/shared/database.py azure/functions/host.json azure/functions/function_app.py ./
