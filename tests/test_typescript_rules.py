"""Unit tests for TypeScript/JavaScript rules."""

import sys
import unittest
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rules.typescript_rules import (
	TypeScriptAnyTypeRule,
	TypeScriptAsyncWithoutTryCatchRule,
	TypeScriptConsoleLogRule,
	TypeScriptUnusedImportRule,
)


class TestConsoleLogRule(unittest.TestCase):
	def setUp(self):
		self.rule = TypeScriptConsoleLogRule()

	def _changed(self, *lines):
		return {i + 1: line for i, line in enumerate(lines)}

	def test_console_log_detected(self):
		comments = self.rule.analyze("src/service.ts", self._changed('  console.log("debug");'))
		self.assertEqual(len(comments), 1)
		self.assertEqual(comments[0].rule_id, "TS-001")
		self.assertEqual(comments[0].severity, "warning")

	def test_console_warn_detected(self):
		comments = self.rule.analyze("src/service.ts", self._changed("console.warn('x');"))
		self.assertEqual(len(comments), 1)

	def test_console_log_in_test_file_passes(self):
		comments = self.rule.analyze("src/service.test.ts", self._changed('console.log("test");'))
		self.assertEqual(len(comments), 0)

	def test_console_log_in_spec_file_passes(self):
		comments = self.rule.analyze("src/__tests__/service.spec.ts", self._changed('console.log("test");'))
		self.assertEqual(len(comments), 0)

	def test_console_log_in_comment_skipped(self):
		comments = self.rule.analyze("src/service.ts", self._changed("// console.log('x')"))
		self.assertEqual(len(comments), 0)

	def test_js_file_detected(self):
		comments = self.rule.analyze("src/utils.js", self._changed("console.debug(obj);"))
		self.assertEqual(len(comments), 1)

	def test_non_ts_file_skipped(self):
		self.assertFalse(self.rule.applies_to("file.cs"))


class TestAnyTypeRule(unittest.TestCase):
	def setUp(self):
		self.rule = TypeScriptAnyTypeRule()

	def _changed(self, *lines):
		return {i + 1: line for i, line in enumerate(lines)}

	def test_any_type_annotation_detected(self):
		comments = self.rule.analyze("src/api.ts", self._changed("function foo(x: any) {}"))
		self.assertEqual(len(comments), 1)
		self.assertEqual(comments[0].rule_id, "TS-003")

	def test_generic_any_detected(self):
		comments = self.rule.analyze("src/api.ts", self._changed("const arr: Array<any> = [];"))
		self.assertEqual(len(comments), 1)

	def test_any_in_comment_skipped(self):
		comments = self.rule.analyze("src/api.ts", self._changed("// param: any"))
		self.assertEqual(len(comments), 0)

	def test_any_only_for_ts_files(self):
		self.assertFalse(self.rule.applies_to("file.js"))
		self.assertTrue(self.rule.applies_to("file.ts"))
		self.assertTrue(self.rule.applies_to("file.tsx"))


class TestAsyncWithoutTryCatchRule(unittest.TestCase):
	def setUp(self):
		self.rule = TypeScriptAsyncWithoutTryCatchRule()

	def _changed(self, *lines):
		return {i + 1: line for i, line in enumerate(lines)}

	def test_await_without_try_detected(self):
		comments = self.rule.analyze("src/service.ts", self._changed(
			"async function fetch() {",
			"  const data = await api.get('/users');",
			"}",
		))
		self.assertEqual(len(comments), 1)
		self.assertEqual(comments[0].rule_id, "TS-002")

	def test_await_with_try_in_changed_lines_passes(self):
		comments = self.rule.analyze("src/service.ts", self._changed(
			"try {",
			"  const data = await api.get('/users');",
			"} catch (e) { logger.error(e); }",
		))
		self.assertEqual(len(comments), 0)

	def test_await_in_comment_skipped(self):
		comments = self.rule.analyze("src/service.ts", self._changed("// await something()"))
		self.assertEqual(len(comments), 0)


class TestUnusedImportRule(unittest.TestCase):
	def setUp(self):
		self.rule = TypeScriptUnusedImportRule()

	def _changed(self, *lines):
		return {i + 1: line for i, line in enumerate(lines)}

	def test_unused_import_detected(self):
		comments = self.rule.analyze("src/component.ts", self._changed(
			"import { Foo, Bar } from './types';",
			"const x = new Bar();",
		))
		# Foo appears only once (in the import line), Bar appears twice
		self.assertTrue(any(c.rule_id == "TS-004" and "Foo" in c.message for c in comments))

	def test_used_import_passes(self):
		comments = self.rule.analyze("src/component.ts", self._changed(
			"import { Foo } from './types';",
			"const x = new Foo();",
			"const y: Foo = null;",
		))
		self.assertEqual(len(comments), 0)

	def test_non_named_import_ignored(self):
		comments = self.rule.analyze("src/component.ts", self._changed(
			"import React from 'react';",
		))
		self.assertEqual(len(comments), 0)


if __name__ == "__main__":
	unittest.main()
