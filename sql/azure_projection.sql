-- Run as the Entra database administrator on the Inventory database.
IF OBJECT_ID(N'dbo.InventoryState', N'U') IS NULL
BEGIN
  CREATE TABLE dbo.InventoryState (
    tenant_id nvarchar(128) COLLATE Latin1_General_100_BIN2 NOT NULL,
    product_id nvarchar(128) COLLATE Latin1_General_100_BIN2 NOT NULL,
    sku nvarchar(128) NOT NULL, parent_id nvarchar(128) NOT NULL,
    stock bigint NOT NULL CHECK (stock >= 0),
    version bigint NOT NULL CHECK (version >= 1),
    deleted bit NOT NULL, changed_at_ms bigint NOT NULL CHECK (changed_at_ms >= 0),
    payload_sha256 char(64) NOT NULL,
    CONSTRAINT PK_InventoryState PRIMARY KEY(tenant_id, product_id)
  );
  CREATE INDEX IX_InventoryState_Active ON dbo.InventoryState(tenant_id) INCLUDE(stock) WHERE deleted=0;
END;
IF OBJECT_ID(N'dbo.ProjectionObservation', N'U') IS NULL
  CREATE TABLE dbo.ProjectionObservation (
    tenant_id nvarchar(128) COLLATE Latin1_General_100_BIN2 NOT NULL,
    product_id nvarchar(128) COLLATE Latin1_General_100_BIN2 NOT NULL,
    version bigint NOT NULL, source_epoch_s bigint NOT NULL, sql_observed_at datetime2(3) NOT NULL,
    PRIMARY KEY(tenant_id, product_id, version)
  );
GO
CREATE OR ALTER PROCEDURE dbo.ApplyInventory
  @tenant nvarchar(128), @product nvarchar(128), @sku nvarchar(128), @parent nvarchar(128),
  @stock bigint, @version bigint, @deleted bit, @changed bigint, @hash char(64), @source_s bigint
AS
BEGIN
  SET NOCOUNT ON;
  SET XACT_ABORT ON;
  DECLARE @previous bigint, @priorHash char(64), @outcome varchar(12);
  BEGIN TRY
    BEGIN TRANSACTION;
    -- A range lock protects first insert, including on an empty table.
    SELECT @previous=version, @priorHash=payload_sha256 FROM dbo.InventoryState WITH(UPDLOCK,HOLDLOCK)
      WHERE tenant_id=@tenant AND product_id=@product;
    IF @previous IS NULL
    BEGIN
      INSERT dbo.InventoryState(tenant_id,product_id,sku,parent_id,stock,version,deleted,changed_at_ms,payload_sha256)
        VALUES(@tenant,@product,@sku,@parent,@stock,@version,@deleted,@changed,@hash);
      SET @outcome='applied';
    END
    ELSE IF @version > @previous
    BEGIN
      UPDATE dbo.InventoryState SET sku=@sku, parent_id=@parent, stock=@stock, version=@version,
        deleted=@deleted, changed_at_ms=@changed, payload_sha256=@hash WHERE tenant_id=@tenant AND product_id=@product;
      SET @outcome='applied';
    END
    ELSE
      SET @outcome=CASE WHEN @version < @previous THEN 'stale'
                        WHEN @priorHash=@hash THEN 'duplicate' ELSE 'conflict' END;
    IF @outcome='applied'
      INSERT dbo.ProjectionObservation(tenant_id, product_id, version, source_epoch_s, sql_observed_at)
        VALUES(@tenant,@product,@version,@source_s,SYSUTCDATETIME());
    COMMIT;
    SELECT @outcome AS outcome;
  END TRY
  BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK;
    THROW;
  END CATCH;
END;
GO
CREATE OR ALTER VIEW dbo.InventoryTotals AS
SELECT tenant_id, SUM(CASE WHEN deleted=0 THEN CONVERT(bigint,1) ELSE 0 END) AS active_products,
       SUM(CASE WHEN deleted=0 THEN CONVERT(decimal(38,0),stock) ELSE 0 END) AS stock_units,
       SUM(CONVERT(bigint,deleted)) AS retained_deletes
FROM dbo.InventoryState GROUP BY tenant_id;
GO
