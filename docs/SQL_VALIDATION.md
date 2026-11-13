# Azure SQL syntax validation

On 14 November 2026 Microsoft ScriptDom parsed the Azure SQL scripts using the T-SQL 160 grammar, Azure SQL engine mode and quoted identifiers. The run substituted two fixed, synthetic GUIDs for SQLCMD variables before parsing. [Microsoft ScriptDom package](https://www.nuget.org/packages/Microsoft.SqlServer.TransactSql.ScriptDom/180.140.0)

| File | Batches | Top-level statements | Syntax errors |
| --- | ---: | ---: | ---: |
| `sql/azure_observation.sql` | 1 | 4 | 0 |
| `sql/azure_permissions.sql` | 1 | 9 | 0 |
| `sql/azure_projection.sql` | 3 | 4 | 0 |
| Total | 5 | 17 | 0 |

These are parser results. Object resolution, generated dynamic SQL, Entra permissions, locking, stored procedure execution and query plans still require execution against a database. The local run used .NET SDK 8.0.428 on macOS arm64 and Microsoft.SqlServer.TransactSql.ScriptDom 180.140.0. Both packages were downloaded into a temporary directory. The ScriptDom NuGet archive had SHA-256 `e41dbe9238694c68d3a165c11880f9332905b4bb9759a604b1797bb3f282d217`. The parser read only local SQL files.

## Reproduce

With .NET SDK 8 on PATH, run from the repository root. Restore downloads the package. The second command runs the parser without contacting any SQL instance or Azure resource.

```sh
check_dir=$(mktemp -d)
cat > "$check_dir/Check.csproj" <<'EOF'
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup><OutputType>Exe</OutputType><TargetFramework>net8.0</TargetFramework></PropertyGroup>
  <ItemGroup><PackageReference Include="Microsoft.SqlServer.TransactSql.ScriptDom" Version="180.140.0" /></ItemGroup>
</Project>
EOF
cat > "$check_dir/Program.cs" <<'EOF'
using System;
using System.IO;
using System.Linq;
using Microsoft.SqlServer.TransactSql.ScriptDom;

var parser = new TSql160Parser(true, SqlEngineType.SqlAzure);
foreach (var path in args.OrderBy(s => s, StringComparer.Ordinal)) {
    var text = File.ReadAllText(path)
        .Replace("$(FUNCTION_PRINCIPAL)", "00000000-0000-0000-0000-000000000001")
        .Replace("$(WEB_PRINCIPAL)", "00000000-0000-0000-0000-000000000002");
    using var reader = new StringReader(text);
    var script = (TSqlScript)parser.Parse(reader, out var errors);
    Console.WriteLine($"{Path.GetFileName(path)}: {script.Batches.Count} batches, " +
                      $"{script.Batches.Sum(b => b.Statements.Count)} top-level statements, {errors.Count} errors");
    foreach (var error in errors)
        Console.Error.WriteLine($"{path}:{error.Line}:{error.Column} {error.Message}");
    if (errors.Count != 0) Environment.ExitCode = 1;
}
EOF
dotnet restore "$check_dir/Check.csproj"
dotnet run --project "$check_dir/Check.csproj" --no-restore -- sql/azure*.sql
```
