from .csharp_rules import CSharpNamingRules, CSharpStyleRules, CSharpAsyncRules, CSharpMagicNumberRules
from .sql_rules import SqlFormattingRules, SqlBestPracticeRules, SqlPerformanceRules
from .general_rules import SecurityRules, GeneralQualityRules, SensitiveDataLoggingRules
from .typescript_rules import (
	TypeScriptConsoleLogRule,
	TypeScriptAsyncWithoutTryCatchRule,
	TypeScriptAnyTypeRule,
	TypeScriptUnusedImportRule,
)

ALL_RULES = [
	CSharpNamingRules(),
	CSharpStyleRules(),
	CSharpAsyncRules(),
	CSharpMagicNumberRules(),
	SqlFormattingRules(),
	SqlBestPracticeRules(),
	SqlPerformanceRules(),
	SecurityRules(),
	GeneralQualityRules(),
	SensitiveDataLoggingRules(),
	TypeScriptConsoleLogRule(),
	TypeScriptAsyncWithoutTryCatchRule(),
	TypeScriptAnyTypeRule(),
	TypeScriptUnusedImportRule(),
]
