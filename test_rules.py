#!/usr/bin/env python3
"""
Unit tests for the code review rules.

Run with: python -m pytest test_rules.py -v
Or simply: python test_rules.py
"""

import unittest

from rules.csharp_rules import CSharpNamingRules, CSharpStyleRules
from rules.sql_rules import SqlFormattingRules, SqlBestPracticeRules
from rules.general_rules import SecurityRules, GeneralQualityRules
from diff_parser import parse_diff, filter_reviewable_files


class TestCSharpNamingRules(unittest.TestCase):
	"""Tests for C# naming convention rules."""

	def setUp(self):
		self.rule = CSharpNamingRules()

	def test_id_lowercase_detected(self):
		"""'customerId' should be flagged — must be 'customerID'."""
		lines = {10: "\t\tint customerId = 0;"}
		comments = self.rule.analyze("Test.cs", lines)
		rule_ids = [c.rule_id for c in comments]
		self.assertIn("CS-NAME-001", rule_ids)

	def test_id_uppercase_passes(self):
		"""'customerID' should not be flagged."""
		lines = {10: "\t\tint customerID = 0;"}
		comments = self.rule.analyze("Test.cs", lines)
		id_comments = [c for c in comments if c.rule_id == "CS-NAME-001"]
		self.assertEqual(len(id_comments), 0)

	def test_private_field_no_underscore(self):
		"""Private field without underscore prefix should be flagged."""
		lines = {5: "\t\tprivate int customerID;"}
		comments = self.rule.analyze("Test.cs", lines)
		rule_ids = [c.rule_id for c in comments]
		self.assertIn("CS-NAME-002", rule_ids)

	def test_private_field_with_underscore_passes(self):
		"""Private field with underscore prefix should pass."""
		lines = {5: "\t\tprivate int _customerID;"}
		comments = self.rule.analyze("Test.cs", lines)
		field_comments = [c for c in comments if c.rule_id == "CS-NAME-002"]
		self.assertEqual(len(field_comments), 0)

	def test_var_with_primitive_detected(self):
		"""'var count = 0;' should be flagged."""
		lines = {15: '\t\tvar count = 0;'}
		comments = self.rule.analyze("Test.cs", lines)
		rule_ids = [c.rule_id for c in comments]
		self.assertIn("CS-NAME-004", rule_ids)

	def test_var_with_primitive_string(self):
		"""'var name = "";' should be flagged."""
		lines = {15: '\t\tvar name = "";'}
		comments = self.rule.analyze("Test.cs", lines)
		rule_ids = [c.rule_id for c in comments]
		self.assertIn("CS-NAME-004", rule_ids)

	def test_var_with_new_passes(self):
		"""'var customer = new Customer();' should pass."""
		lines = {15: "\t\tvar customer = new Customer();"}
		comments = self.rule.analyze("Test.cs", lines)
		var_comments = [c for c in comments if c.rule_id == "CS-NAME-004"]
		self.assertEqual(len(var_comments), 0)

	def test_hungarian_notation_detected(self):
		"""'int iCustomer' should be flagged."""
		lines = {20: "\t\tint iCustomer = 0;"}
		comments = self.rule.analyze("Test.cs", lines)
		rule_ids = [c.rule_id for c in comments]
		self.assertIn("CS-NAME-003", rule_ids)

	def test_comments_are_skipped(self):
		"""Lines that are comments should not be analyzed."""
		lines = {10: "\t\t// int customerId = 0;"}
		comments = self.rule.analyze("Test.cs", lines)
		self.assertEqual(len(comments), 0)


class TestCSharpStyleRules(unittest.TestCase):
	"""Tests for C# style rules."""

	def setUp(self):
		self.rule = CSharpStyleRules()

	def test_this_qualifier_detected(self):
		"""'this.field' should be flagged."""
		lines = {10: "\t\tthis.customerID = value;"}
		comments = self.rule.analyze("Test.cs", lines)
		rule_ids = [c.rule_id for c in comments]
		self.assertIn("CS-STYLE-002", rule_ids)

	def test_entity_framework_detected(self):
		"""Entity Framework usage should be flagged as error."""
		lines = {10: "\t\tprivate DbContext _context;"}
		comments = self.rule.analyze("Test.cs", lines)
		ef_comments = [c for c in comments if c.rule_id == "CS-STYLE-004"]
		self.assertTrue(len(ef_comments) > 0)
		self.assertEqual(ef_comments[0].severity, "error")

	def test_line_length_warning(self):
		"""Lines over 138 characters should be flagged."""
		long_line = "\t" + "x" * 140
		lines = {10: long_line}
		comments = self.rule.analyze("Test.cs", lines)
		rule_ids = [c.rule_id for c in comments]
		self.assertIn("CS-STYLE-001", rule_ids)


class TestSqlFormattingRules(unittest.TestCase):
	"""Tests for SQL formatting rules."""

	def setUp(self):
		self.rule = SqlFormattingRules()

	def test_outer_join_detected(self):
		"""LEFT OUTER JOIN should be flagged."""
		lines = {10: "\tLEFT OUTER JOIN CustomerUser cu ON cu.customerID = c.customerID"}
		comments = self.rule.analyze("Test.sql", lines)
		rule_ids = [c.rule_id for c in comments]
		self.assertIn("SQL-FMT-003", rule_ids)

	def test_left_join_passes(self):
		"""LEFT JOIN should not be flagged."""
		lines = {10: "\tLEFT JOIN CustomerUser cu ON cu.customerID = c.customerID"}
		comments = self.rule.analyze("Test.sql", lines)
		outer_comments = [c for c in comments if c.rule_id == "SQL-FMT-003"]
		self.assertEqual(len(outer_comments), 0)

	def test_dbo_brackets_detected(self):
		"""[dbo].[Table] should be flagged in non-CREATE statements."""
		lines = {10: "\tINSERT INTO [dbo].[Customer] (customerID) VALUES (1)"}
		comments = self.rule.analyze("Test.sql", lines)
		rule_ids = [c.rule_id for c in comments]
		self.assertIn("SQL-FMT-004", rule_ids)


class TestSqlBestPracticeRules(unittest.TestCase):
	"""Tests for SQL best practice rules."""

	def setUp(self):
		self.rule = SqlBestPracticeRules()

	def test_at_identity_detected(self):
		"""@@IDENTITY should be flagged — use SCOPE_IDENTITY()."""
		lines = {10: "\tSELECT @customerID = @@IDENTITY"}
		comments = self.rule.analyze("Test.sql", lines)
		rule_ids = [c.rule_id for c in comments]
		self.assertIn("SQL-BP-001", rule_ids)
		# Should be an error, not just a warning
		self.assertEqual(comments[0].severity, "error")

	def test_scope_identity_passes(self):
		"""SCOPE_IDENTITY() should not be flagged."""
		lines = {10: "\tSELECT @customerID = SCOPE_IDENTITY()"}
		comments = self.rule.analyze("Test.sql", lines)
		identity_comments = [c for c in comments if c.rule_id == "SQL-BP-001"]
		self.assertEqual(len(identity_comments), 0)

	def test_not_is_null_detected(self):
		"""NOT @var IS NULL should be flagged."""
		lines = {10: "\tIF (NOT @someArgument IS NULL)"}
		comments = self.rule.analyze("Test.sql", lines)
		rule_ids = [c.rule_id for c in comments]
		self.assertIn("SQL-BP-002", rule_ids)


class TestSecurityRules(unittest.TestCase):
	"""Tests for security rules."""

	def setUp(self):
		self.rule = SecurityRules()

	def test_hardcoded_password_detected(self):
		"""Hardcoded password strings should be flagged."""
		lines = {10: '\t\tstring password = "SuperSecret123";'}
		comments = self.rule.analyze("Config.cs", lines)
		sec_comments = [c for c in comments if c.rule_id == "SEC-002"]
		self.assertTrue(len(sec_comments) > 0)

	def test_sql_injection_detected(self):
		"""SQL string concatenation should be flagged."""
		lines = {10: '\t\tsql.ExecuteStoredProcedure("GetUser" + userInput);'}
		comments = self.rule.analyze("Service.cs", lines)
		injection_comments = [c for c in comments if c.rule_id == "SEC-003"]
		self.assertTrue(len(injection_comments) > 0)

	def test_sensitive_file_warning(self):
		"""Sensitive files should get a warning."""
		lines = {1: "some content"}
		comments = self.rule.analyze("appsettings.Production.json", lines)
		file_comments = [c for c in comments if c.rule_id == "SEC-001"]
		self.assertTrue(len(file_comments) > 0)


class TestGeneralQualityRules(unittest.TestCase):
	"""Tests for general quality rules."""

	def setUp(self):
		self.rule = GeneralQualityRules()

	def test_console_writeline_in_production(self):
		"""Console.WriteLine in non-test code should be flagged."""
		lines = {10: '\t\tConsole.WriteLine("debug");'}
		comments = self.rule.analyze("Web/api.multitracks.com/Service.cs", lines)
		console_comments = [c for c in comments if c.rule_id == "GEN-002"]
		self.assertTrue(len(console_comments) > 0)

	def test_console_writeline_in_tests_passes(self):
		"""Console.WriteLine in test code should not be flagged."""
		lines = {10: '\t\tConsole.WriteLine("debug");'}
		comments = self.rule.analyze("Tests.Core/SomeTest.cs", lines)
		console_comments = [c for c in comments if c.rule_id == "GEN-002"]
		self.assertEqual(len(console_comments), 0)

	def test_debugger_statement_detected(self):
		"""Debugger.Break() should be flagged as error."""
		lines = {10: "\t\tDebugger.Break();"}
		comments = self.rule.analyze("Service.cs", lines)
		debug_comments = [c for c in comments if c.rule_id == "GEN-003"]
		self.assertTrue(len(debug_comments) > 0)
		self.assertEqual(debug_comments[0].severity, "error")


class TestDiffParser(unittest.TestCase):
	"""Tests for the diff parser."""

	def test_parse_simple_diff(self):
		"""Parse a simple unified diff with one added line."""
		diff = """diff --git a/Test.cs b/Test.cs
--- a/Test.cs
+++ b/Test.cs
@@ -10,6 +10,7 @@ namespace Test
 {
     class Foo
     {
+        private int customerId;
     }
 }
"""
		files = parse_diff(diff)
		self.assertEqual(len(files), 1)
		self.assertEqual(files[0].path, "Test.cs")
		self.assertIn(13, files[0].changed_lines)
		self.assertIn("customerId", files[0].changed_lines[13])

	def test_filter_excludes_binaries(self):
		"""Binary files should be filtered out."""
		from diff_parser import ChangedFile
		files = [
			ChangedFile(path="test.dll", changed_lines={1: "binary"}),
			ChangedFile(path="Service.cs", changed_lines={1: "code"}),
			ChangedFile(path="image.png", changed_lines={1: "binary"}),
		]
		filtered = filter_reviewable_files(files)
		self.assertEqual(len(filtered), 1)
		self.assertEqual(filtered[0].path, "Service.cs")

	def test_filter_excludes_packages(self):
		"""Package directories should be filtered out."""
		from diff_parser import ChangedFile
		files = [
			ChangedFile(path="packages/NuGet/lib.cs", changed_lines={1: "code"}),
			ChangedFile(path="Core/Service.cs", changed_lines={1: "code"}),
		]
		filtered = filter_reviewable_files(files)
		self.assertEqual(len(filtered), 1)
		self.assertEqual(filtered[0].path, "Core/Service.cs")


class TestCSharpAsyncRules(unittest.TestCase):
	"""Tests for C# async/await rules (CS-ASYNC-*)."""

	def setUp(self):
		try:
			from rules.csharp_rules import CSharpAsyncRules, CSharpMagicNumberRules
			self.rule = CSharpAsyncRules()
			self.magic_rule = CSharpMagicNumberRules()
			self.available = True
		except ImportError:
			self.available = False
			self.magic_rule = None

	def _skip_if_unavailable(self):
		if not self.available:
			self.skipTest("CSharpAsyncRules not yet implemented")

	def test_async_void_detected(self):
		"""async void method should be flagged with CS-ASYNC-001."""
		self._skip_if_unavailable()
		lines = {5: "\t\tpublic async void DoSomething()"}
		issues = self.rule.analyze("Service.cs", lines)
		self.assertTrue(
			any(i.rule_id == "CS-ASYNC-001" for i in issues),
			"Expected CS-ASYNC-001 for 'async void DoSomething()'"
		)

	def test_async_task_ok(self):
		"""async Task method should NOT be flagged with CS-ASYNC-001."""
		self._skip_if_unavailable()
		lines = {5: "\t\tpublic async Task DoSomethingAsync()"}
		issues = self.rule.analyze("Service.cs", lines)
		self.assertFalse(
			any(i.rule_id == "CS-ASYNC-001" for i in issues),
			"async Task should not trigger CS-ASYNC-001"
		)

	def test_async_naming_missing(self):
		"""async Task without 'Async' suffix should be flagged with CS-ASYNC-002."""
		self._skip_if_unavailable()
		lines = {10: "\t\tpublic async Task GetData()"}
		issues = self.rule.analyze("Service.cs", lines)
		self.assertTrue(
			any(i.rule_id == "CS-ASYNC-002" for i in issues),
			"Expected CS-ASYNC-002 for async method missing 'Async' suffix"
		)

	def test_async_naming_ok(self):
		"""async Task with 'Async' suffix should NOT be flagged with CS-ASYNC-002."""
		self._skip_if_unavailable()
		lines = {10: "\t\tpublic async Task GetDataAsync()"}
		issues = self.rule.analyze("Service.cs", lines)
		self.assertFalse(
			any(i.rule_id == "CS-ASYNC-002" for i in issues),
			"Properly named async method should not trigger CS-ASYNC-002"
		)

	def test_async_event_handler_ok(self):
		"""async void event handler should NOT be flagged with CS-ASYNC-001."""
		self._skip_if_unavailable()
		# Event handlers are a valid exception to the async void rule.
		lines = {15: "\t\tprivate async void OnButtonClick(object sender, EventArgs e)"}
		issues = self.rule.analyze("Form.cs", lines)
		self.assertFalse(
			any(i.rule_id == "CS-ASYNC-001" for i in issues),
			"Event handler 'async void OnButtonClick' should not trigger CS-ASYNC-001"
		)

	def test_magic_number_detected(self):
		"""Inline numeric literal should be flagged with CS-MAGIC-001."""
		self._skip_if_unavailable()
		lines = {20: "\t\tint timeout = 3600;"}
		issues = self.magic_rule.analyze("Service.cs", lines)
		self.assertTrue(
			any(i.rule_id == "CS-MAGIC-001" for i in issues),
			"Expected CS-MAGIC-001 for magic number 3600"
		)

	def test_magic_number_ok(self):
		"""Constant declaration should NOT be flagged with CS-MAGIC-001."""
		self._skip_if_unavailable()
		lines = {20: "\t\tconst int MaxRetries = 3;"}
		issues = self.magic_rule.analyze("Service.cs", lines)
		self.assertFalse(
			any(i.rule_id == "CS-MAGIC-001" for i in issues),
			"const declaration should not trigger CS-MAGIC-001"
		)


class TestSqlPerfRules(unittest.TestCase):
	"""Tests for SQL performance rules (SQL-PERF-*) and SQL-BP-005."""

	def setUp(self):
		# SqlPerfRules will be implemented by a parallel agent.
		try:
			from rules.sql_rules import SqlPerfRules
			self.perf_rule = SqlPerfRules()
			self.perf_available = True
		except ImportError:
			self.perf_available = False

		self.bp_rule = SqlBestPracticeRules()

	def _skip_if_perf_unavailable(self):
		if not self.perf_available:
			self.skipTest("SqlPerfRules not yet implemented")

	def test_select_star_detected(self):
		"""SELECT * should be flagged with SQL-PERF-001."""
		self._skip_if_perf_unavailable()
		lines = {5: "\tSELECT * FROM Users"}
		issues = self.perf_rule.analyze("Query.sql", lines)
		self.assertTrue(
			any(i.rule_id == "SQL-PERF-001" for i in issues),
			"Expected SQL-PERF-001 for 'SELECT * FROM Users'"
		)

	def test_select_star_top_detected(self):
		"""SELECT TOP N * should also be flagged with SQL-PERF-001."""
		self._skip_if_perf_unavailable()
		lines = {5: "\tSELECT TOP 10 * FROM Users"}
		issues = self.perf_rule.analyze("Query.sql", lines)
		self.assertTrue(
			any(i.rule_id == "SQL-PERF-001" for i in issues),
			"Expected SQL-PERF-001 for 'SELECT TOP 10 * FROM Users'"
		)

	def test_select_columns_ok(self):
		"""SELECT with explicit columns should NOT be flagged with SQL-PERF-001."""
		self._skip_if_perf_unavailable()
		lines = {5: "\tSELECT Id, Name FROM Users"}
		issues = self.perf_rule.analyze("Query.sql", lines)
		self.assertFalse(
			any(i.rule_id == "SQL-PERF-001" for i in issues),
			"SELECT with named columns should not trigger SQL-PERF-001"
		)

	def test_cursor_detected(self):
		"""CURSOR declaration should be flagged with SQL-PERF-002."""
		self._skip_if_perf_unavailable()
		lines = {10: "\tDECLARE myCursor CURSOR FOR"}
		issues = self.perf_rule.analyze("Proc.sql", lines)
		self.assertTrue(
			any(i.rule_id == "SQL-PERF-002" for i in issues),
			"Expected SQL-PERF-002 for CURSOR declaration"
		)

	def test_no_cursor_ok(self):
		"""DECLARE for a variable should NOT be flagged with SQL-PERF-002."""
		self._skip_if_perf_unavailable()
		lines = {10: "\tDECLARE @myVar INT"}
		issues = self.perf_rule.analyze("Proc.sql", lines)
		self.assertFalse(
			any(i.rule_id == "SQL-PERF-002" for i in issues),
			"Variable DECLARE should not trigger SQL-PERF-002"
		)

	def test_tran_without_rollback(self):
		"""BEGIN TRANSACTION without ROLLBACK should be flagged with SQL-BP-005."""
		self._skip_if_perf_unavailable()
		diff_content = (
			"diff --git a/Proc.sql b/Proc.sql\n"
			"--- a/Proc.sql\n"
			"+++ b/Proc.sql\n"
			"@@ -1,4 +1,4 @@\n"
			"+BEGIN TRANSACTION\n"
			"+    UPDATE Users SET Active = 1 WHERE UserID = 1\n"
			"+COMMIT\n"
		)
		from diff_parser import parse_diff
		files = parse_diff(diff_content)
		self.assertEqual(len(files), 1)
		issues = self.perf_rule.analyze(files[0].path, files[0].changed_lines)
		self.assertTrue(
			any(i.rule_id == "SQL-BP-005" for i in issues),
			"Expected SQL-BP-005 when BEGIN TRANSACTION has no ROLLBACK"
		)

	def test_tran_with_rollback_ok(self):
		"""BEGIN TRANSACTION with ROLLBACK should NOT be flagged with SQL-BP-005."""
		self._skip_if_perf_unavailable()
		diff_content = (
			"diff --git a/Proc.sql b/Proc.sql\n"
			"--- a/Proc.sql\n"
			"+++ b/Proc.sql\n"
			"@@ -1,6 +1,6 @@\n"
			"+BEGIN TRANSACTION\n"
			"+    UPDATE Users SET Active = 1 WHERE UserID = 1\n"
			"+    IF @@ERROR <> 0\n"
			"+        ROLLBACK\n"
			"+    ELSE\n"
			"+        COMMIT\n"
		)
		from diff_parser import parse_diff
		files = parse_diff(diff_content)
		self.assertEqual(len(files), 1)
		issues = self.perf_rule.analyze(files[0].path, files[0].changed_lines)
		self.assertFalse(
			any(i.rule_id == "SQL-BP-005" for i in issues),
			"SQL-BP-005 should not fire when ROLLBACK is present"
		)


class TestSecuritySensitiveLogging(unittest.TestCase):
	"""Tests for sensitive data in log statements (SEC-006)."""

	def setUp(self):
		from rules.general_rules import SensitiveDataLoggingRules
		self.rule = SensitiveDataLoggingRules()

	def test_password_in_log_detected(self):
		"""Logging a password value should be flagged with SEC-006."""
		lines = {8: '\t\tlogger.Info("Password: " + password);'}
		issues = self.rule.analyze("AuthService.cs", lines)
		self.assertTrue(
			any(i.rule_id == "SEC-006" for i in issues),
			"Expected SEC-006 for logging a password variable"
		)

	def test_token_in_log_detected(self):
		"""Logging a token value should be flagged with SEC-006."""
		lines = {12: '\t\tlog.Debug($"Token={apiToken}");'}
		issues = self.rule.analyze("TokenService.cs", lines)
		self.assertTrue(
			any(i.rule_id == "SEC-006" for i in issues),
			"Expected SEC-006 for logging an API token"
		)

	def test_normal_log_ok(self):
		"""Logging a non-sensitive message should NOT be flagged with SEC-006."""
		lines = {5: '\t\tlogger.Info("User logged in successfully");'}
		issues = self.rule.analyze("AuthService.cs", lines)
		self.assertFalse(
			any(i.rule_id == "SEC-006" for i in issues),
			"Non-sensitive log message should not trigger SEC-006"
		)


class TestEdgeCases(unittest.TestCase):
	"""Tests for edge cases in the diff parser and rule engine."""

	def test_empty_diff(self):
		"""parse_diff on an empty string should return an empty list."""
		from diff_parser import parse_diff
		files = parse_diff("")
		self.assertEqual(files, [], "Empty diff should produce no ChangedFile objects")

	def test_diff_with_only_deletions(self):
		"""A diff with only removed lines should produce a ChangedFile with no added_lines."""
		from diff_parser import parse_diff
		diff_content = (
			"diff --git a/Service.cs b/Service.cs\n"
			"--- a/Service.cs\n"
			"+++ b/Service.cs\n"
			"@@ -5,3 +5,0 @@ namespace Test\n"
			"-        private int _oldField;\n"
			"-        private int _otherField;\n"
			"-        private int _lastField;\n"
		)
		files = parse_diff(diff_content)
		# parse_diff only appends a ChangedFile when it has changed_lines,
		# so a deletion-only diff produces an empty list.
		self.assertEqual(
			len(files), 0,
			"Deletion-only diff should result in no ChangedFile with changed lines"
		)

	def test_filter_binary_files(self):
		"""filter_reviewable_files should exclude .dll files."""
		from diff_parser import ChangedFile, filter_reviewable_files
		files = [
			ChangedFile(path="MyLibrary.dll", changed_lines={1: "binary content"}),
			ChangedFile(path="Service.cs", changed_lines={1: "public class Service {}"}),
		]
		filtered = filter_reviewable_files(files)
		paths = [f.path for f in filtered]
		self.assertNotIn("MyLibrary.dll", paths, ".dll must be excluded")
		self.assertIn("Service.cs", paths, ".cs must be included")

	def test_filter_node_modules(self):
		"""filter_reviewable_files should exclude files inside node_modules/."""
		from diff_parser import ChangedFile, filter_reviewable_files
		files = [
			ChangedFile(path="node_modules/foo.js", changed_lines={1: "module.exports = {};"}),
			ChangedFile(path="src/app.js", changed_lines={1: "const x = 1;"}),
		]
		filtered = filter_reviewable_files(files)
		paths = [f.path for f in filtered]
		self.assertNotIn("node_modules/foo.js", paths, "node_modules must be excluded")
		self.assertIn("src/app.js", paths, "src/app.js must be included")

	def test_reviewable_cs_file(self):
		"""filter_reviewable_files should include .cs files."""
		from diff_parser import ChangedFile, filter_reviewable_files
		files = [
			ChangedFile(path="Core/CustomerService.cs", changed_lines={1: "public class CustomerService {}"}),
		]
		filtered = filter_reviewable_files(files)
		self.assertEqual(len(filtered), 1)
		self.assertEqual(filtered[0].path, "Core/CustomerService.cs")

	def test_unicode_in_code(self):
		"""Rules should not crash when a changed line contains Unicode characters."""
		from rules.csharp_rules import CSharpNamingRules, CSharpStyleRules
		from rules.general_rules import SecurityRules, GeneralQualityRules
		unicode_line = "\t\tstring greeting = \"Héllo Wörld — こんにちは\";  // Unicode comment"
		lines = {1: unicode_line}
		for rule_cls in (CSharpNamingRules, CSharpStyleRules, SecurityRules):
			rule = rule_cls()
			try:
				rule.analyze("Service.cs", lines)
			except Exception as exc:
				self.fail(f"{rule_cls.__name__} raised {type(exc).__name__} on Unicode input: {exc}")

	def test_very_long_line(self):
		"""A 200-character line in a .cs file should trigger CS-STYLE-001."""
		from rules.csharp_rules import CSharpStyleRules
		rule = CSharpStyleRules()
		long_line = "        " + "x" * 192  # 8 spaces + 192 x's = 200 chars
		lines = {1: long_line}
		issues = rule.analyze("Service.cs", lines)
		self.assertTrue(
			any(i.rule_id == "CS-STYLE-001" for i in issues),
			"Expected CS-STYLE-001 for a 200-character line"
		)


class TestSeverity(unittest.TestCase):
	"""Tests that rules emit the correct severity level."""

	def test_entity_framework_is_error(self):
		"""DbContext usage should produce an ERROR severity finding."""
		from rules.csharp_rules import CSharpStyleRules
		rule = CSharpStyleRules()
		lines = {10: "\t\tprivate DbContext _context;"}
		issues = rule.analyze("Repository.cs", lines)
		ef_issues = [i for i in issues if i.rule_id == "CS-STYLE-004"]
		self.assertTrue(len(ef_issues) > 0, "CS-STYLE-004 should be emitted for DbContext")
		self.assertEqual(ef_issues[0].severity, "error", "CS-STYLE-004 must have severity 'error'")

	def test_hardcoded_credential_is_error(self):
		"""Hardcoded password assignment should produce an ERROR severity finding."""
		from rules.general_rules import SecurityRules
		rule = SecurityRules()
		lines = {5: '\t\tstring password = "SuperSecret123";'}
		issues = rule.analyze("Config.cs", lines)
		sec_issues = [i for i in issues if i.rule_id == "SEC-002"]
		self.assertTrue(len(sec_issues) > 0, "SEC-002 should be emitted for hardcoded credential")
		self.assertEqual(sec_issues[0].severity, "error", "SEC-002 must have severity 'error'")

	def test_line_length_is_warning_or_suggestion(self):
		"""A line over 138 chars in a .cs file should produce a non-error severity."""
		from rules.csharp_rules import CSharpStyleRules
		rule = CSharpStyleRules()
		# 150 chars of plain text (no tabs, so expandtabs has no effect)
		long_line = " " * 4 + "x" * 146  # 4 spaces + 146 x's = 150 chars
		lines = {1: long_line}
		issues = rule.analyze("Service.cs", lines)
		style_issues = [i for i in issues if i.rule_id == "CS-STYLE-001"]
		self.assertTrue(len(style_issues) > 0, "CS-STYLE-001 should be emitted for a 150-char line")
		self.assertIn(
			style_issues[0].severity, ("warning", "suggestion"),
			"CS-STYLE-001 severity must be 'warning' or 'suggestion', not 'error'"
		)

	def test_magic_number_is_suggestion(self):
		"""Magic number finding should be a SUGGESTION (not error/warning)."""
		try:
			from rules.csharp_rules import CSharpMagicNumberRules
			rule = CSharpMagicNumberRules()
		except ImportError:
			self.skipTest("CSharpMagicNumberRules not yet implemented")

		lines = {10: "\t\tint timeout = 3600;"}
		issues = rule.analyze("Service.cs", lines)
		magic_issues = [i for i in issues if i.rule_id == "CS-MAGIC-001"]
		self.assertTrue(len(magic_issues) > 0, "CS-MAGIC-001 should be emitted for magic number 3600")
		self.assertEqual(
			magic_issues[0].severity, "suggestion",
			"CS-MAGIC-001 must have severity 'suggestion'"
		)


class TestDeduplication(unittest.TestCase):
	"""Tests for ReviewComment deduplication in CodeReviewer."""

	def _make_reviewer(self):
		"""Instantiate CodeReviewer with a minimal stub GitHubClient."""
		from unittest.mock import MagicMock
		from reviewer import CodeReviewer
		stub_client = MagicMock()
		reviewer = CodeReviewer(github_client=stub_client)
		return reviewer

	def test_same_finding_not_duplicated(self):
		"""Two identical ReviewComments (same file, line, rule) should be deduplicated to one."""
		from rules.base import ReviewComment
		reviewer = self._make_reviewer()

		duplicate_comment = ReviewComment(
			file_path="Service.cs",
			line_number=10,
			message="Some issue",
			severity="warning",
			rule_id="CS-NAME-001",
		)
		# Feed the same comment twice
		comments = [duplicate_comment, duplicate_comment]
		deduped = reviewer._deduplicate(comments)
		self.assertEqual(
			len(deduped), 1,
			"Duplicate ReviewComments should be collapsed to a single entry"
		)

	def test_different_rules_not_deduplicated(self):
		"""Two comments on the same line with different rule_ids must both be kept."""
		from rules.base import ReviewComment
		reviewer = self._make_reviewer()

		comment_a = ReviewComment(
			file_path="Service.cs",
			line_number=10,
			message="Issue A",
			severity="warning",
			rule_id="CS-NAME-001",
		)
		comment_b = ReviewComment(
			file_path="Service.cs",
			line_number=10,
			message="Issue B",
			severity="warning",
			rule_id="CS-NAME-002",
		)
		deduped = reviewer._deduplicate([comment_a, comment_b])
		self.assertEqual(
			len(deduped), 2,
			"Comments with different rule_ids on the same line must not be merged"
		)

	def test_different_lines_not_deduplicated(self):
		"""Same rule on different lines must produce two separate comments."""
		from rules.base import ReviewComment
		reviewer = self._make_reviewer()

		comment_line5 = ReviewComment(
			file_path="Service.cs",
			line_number=5,
			message="Issue on line 5",
			severity="warning",
			rule_id="CS-NAME-001",
		)
		comment_line6 = ReviewComment(
			file_path="Service.cs",
			line_number=6,
			message="Issue on line 6",
			severity="warning",
			rule_id="CS-NAME-001",
		)
		deduped = reviewer._deduplicate([comment_line5, comment_line6])
		self.assertEqual(
			len(deduped), 2,
			"Same rule on different lines must not be deduplicated"
		)


if __name__ == "__main__":
	unittest.main(verbosity=2)
