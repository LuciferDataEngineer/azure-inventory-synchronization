-- sqlcmd variables must be the system identities' principal GUIDs from Bicep outputs.
-- Binary SIDs avoid Graph lookup permissions. Names are database-local aliases.
DECLARE @fn uniqueidentifier=CONVERT(uniqueidentifier,'$(FUNCTION_PRINCIPAL)'), @web uniqueidentifier=CONVERT(uniqueidentifier,'$(WEB_PRINCIPAL)');
DECLARE @sid varbinary(16)=CONVERT(varbinary(16),@fn), @webSid varbinary(16)=CONVERT(varbinary(16),@web);
IF EXISTS(SELECT 1 FROM sys.database_principals WHERE name=N'inventory_projector' AND sid<>@sid)
 THROW 51000, 'Existing function principal has a different SID', 1;
IF EXISTS(SELECT 1 FROM sys.database_principals WHERE name=N'inventory_dashboard' AND sid<>@webSid)
 THROW 51001, 'Existing dashboard principal has a different SID', 1;
DECLARE @statement nvarchar(180);
IF DATABASE_PRINCIPAL_ID(N'inventory_projector') IS NULL
BEGIN
 SET @statement=N'CREATE USER inventory_projector WITH SID='+CONVERT(varchar(34),@sid,1)+N', TYPE=E';
 EXEC sp_executesql @statement;
END;
IF DATABASE_PRINCIPAL_ID(N'inventory_dashboard') IS NULL
BEGIN
 SET @statement=N'CREATE USER inventory_dashboard WITH SID='+CONVERT(varchar(34),@webSid,1)+N', TYPE=E';
 EXEC sp_executesql @statement;
END;
GRANT EXECUTE ON OBJECT::dbo.ApplyInventory TO inventory_projector;
GRANT SELECT ON OBJECT::dbo.InventoryTotals TO inventory_dashboard;
GO
