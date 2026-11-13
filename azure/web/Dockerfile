FROM python:3.12-slim-bookworm
COPY azure/shared/install_odbc.sh /tmp/install_odbc.sh
RUN sh /tmp/install_odbc.sh
WORKDIR /app
COPY azure/web/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY azure/shared/database.py azure/web/app.py ./
USER 65534
EXPOSE 8000
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "app:app"]
