"""A pooled TLS connection to Azure SQL using the hosting app's system identity."""
import os


def connect():
    import pyodbc

    host = os.environ["SQL_SERVER"]
    return pyodbc.connect(
        "Driver={ODBC Driver 18 for SQL Server};"
        f"Server=tcp:{host},1433;Database=Inventory;"
        "Encrypt=yes;TrustServerCertificate=no;Authentication=ActiveDirectoryMsi;",
        autocommit=True,
        timeout=15,
    )
